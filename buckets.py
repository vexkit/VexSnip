#!/usr/bin/env python3
"""VexUserCheck v3.2 - Discord rate-limit bucket manager.

Discord enforces rate limits per *route template* rather than per IP or
token. Each route has a bucket ID (from X-RateLimit-Bucket) with its own
remaining/reset counters. This module tracks every bucket we encounter,
and enforces "do not exceed remaining" at the call site.

Sources:
  · Discord docs: "per-route limits shared across similar endpoints,
    indicated in the X-RateLimit-Bucket header. Use this header as a
    unique identifier" [0†L5-L9].
  · X-RateLimit-Reset-After is in seconds, may have decimals [1†L27-L29].
  · X-RateLimit-Scope: global marks a global lock [1†L19-L23].
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Safe margin: never use the last slot of a bucket
_SAFETY_MARGIN = 1

# Fallback bucket lifetimes when headers are missing
_DEFAULT_RESET_AFTER = 1.0

# Global bucket cooldown when we get X-RateLimit-Scope: global
_GLOBAL_COOLDOWN = 1.0


# ---------------------------------------------------------------------------
# Per-bucket state
# ---------------------------------------------------------------------------

@dataclass
class RateLimitBucket:
    """State for a single Discord rate-limit bucket."""

    bucket_id: str
    route: str
    limit: int = 5
    remaining: int = 5
    reset_at: float = 0.0       # monotonic time when window resets
    scope: str = "user"         # user / shared / global
    # Observability
    observed_429: int = 0
    observed_calls: int = 0

    def is_exhausted(self) -> bool:
        """True if we should not send right now."""
        if self.remaining <= _SAFETY_MARGIN:
            return time.monotonic() < self.reset_at
        return False

    def wait_seconds(self) -> float:
        """Seconds to wait before the next call."""
        if not self.is_exhausted():
            return 0.0
        return max(0.0, self.reset_at - time.monotonic())


# ---------------------------------------------------------------------------
# Bucket manager
# ---------------------------------------------------------------------------

class BucketManager:
    """Tracks and enforces Discord rate-limit buckets.

    Usage:
        bucket = BucketManager()
        async with bucket.acquire("POST /unique-username/username-attempt"):
            async with session.post(...) as resp:
                bucket.update_from_headers(route, resp.headers, resp.status)
    """

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitBucket] = {}
        self._route_to_bucket: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._global_lock_until: float = 0.0
        # Observability
        self._total_429: int = 0
        self._total_waits: int = 0

    # -- Public: acquire / release --

    async def acquire(self, route: str) -> None:
        """Block until the bucket for *route* has capacity.

        Does NOT reserve a slot — call update_from_headers() after the
        response to keep state current.
        """
        # Global lock takes priority
        now = time.monotonic()
        if now < self._global_lock_until:
            wait = self._global_lock_until - now
            self._total_waits += 1
            await asyncio.sleep(wait)

        async with self._lock:
            bucket = self._resolve_bucket_locked(route)
            wait = bucket.wait_seconds()

        if wait > 0:
            self._total_waits += 1
            await asyncio.sleep(wait)

    # -- Public: update from response --

    def update_from_headers(
        self,
        route: str,
        headers: dict,
        status: int = 200,
    ) -> None:
        """Update bucket state from Discord response headers."""
        # Global bucket
        scope = _get_header(headers, "X-RateLimit-Scope")
        if scope == "global":
            self._global_lock_until = time.monotonic() + _GLOBAL_COOLDOWN
            self._total_429 += 1
            return

        # Resolve bucket ID
        bucket_id = _get_header(headers, "X-RateLimit-Bucket")
        if not bucket_id:
            # Some endpoints don't return bucket IDs on failure
            bucket_id = self._route_to_bucket.get(route)
            if not bucket_id:
                return  # No bucket info; keep as-is

        # Read counters
        remaining = _parse_int(_get_header(headers, "X-RateLimit-Remaining"))
        limit = _parse_int(_get_header(headers, "X-RateLimit-Limit"))
        reset_after = _parse_float(_get_header(headers, "X-RateLimit-Reset-After"))

        self._apply_locked(
            route=route,
            bucket_id=bucket_id,
            remaining=remaining,
            limit=limit,
            reset_after=reset_after,
            scope=scope or "user",
            status=status,
        )

    def apply_429(
        self,
        route: str,
        retry_after: float,
        scope: str = "user",
    ) -> None:
        """Manually apply a 429 response (from JSON body)."""
        if scope == "global":
            self._global_lock_until = time.monotonic() + max(retry_after, _GLOBAL_COOLDOWN)
            self._total_429 += 1
            return

        bucket_id = self._route_to_bucket.get(route)
        if not bucket_id:
            bucket_id = f"adhoc:{route}"

        self._apply_locked(
            route=route,
            bucket_id=bucket_id,
            remaining=0,
            limit=None,
            reset_after=max(retry_after, 0.1),
            scope=scope,
            status=429,
        )

    # -- Observability --

    def snapshot(self) -> dict:
        now = time.monotonic()
        active = 0
        min_remaining = 999
        soonest_reset = 999.0
        for b in self._buckets.values():
            if b.is_exhausted():
                active += 1
                if b.remaining < min_remaining:
                    min_remaining = b.remaining
                w = b.wait_seconds()
                if w < soonest_reset:
                    soonest_reset = w
        global_wait = max(0.0, self._global_lock_until - now)
        return {
            "buckets": len(self._buckets),
            "active": active,
            "min_remaining": min_remaining if active else 0,
            "soonest_reset": soonest_reset if active else 0.0,
            "global_wait": global_wait,
            "total_429": self._total_429,
            "total_waits": self._total_waits,
        }

    def reset(self) -> None:
        """Clear all bucket state (call between runs)."""
        self._buckets.clear()
        self._route_to_bucket.clear()
        self._global_lock_until = 0.0
        self._total_429 = 0
        self._total_waits = 0

    # -- Internal --

    def _resolve_bucket_locked(self, route: str) -> RateLimitBucket:
        """Return the bucket for *route*, creating if absent."""
        bucket_id = self._route_to_bucket.get(route)
        if bucket_id and bucket_id in self._buckets:
            return self._buckets[bucket_id]

        # Create a new optimistic bucket
        bucket_id = bucket_id or f"pending:{route}"
        b = RateLimitBucket(bucket_id=bucket_id, route=route)
        self._buckets[bucket_id] = b
        self._route_to_bucket[route] = bucket_id
        return b

    def _apply_locked(
        self,
        route: str,
        bucket_id: str,
        remaining: int | None,
        limit: int | None,
        reset_after: float | None,
        scope: str,
        status: int,
    ) -> None:
        b = self._buckets.get(bucket_id)
        if b is None:
            b = RateLimitBucket(bucket_id=bucket_id, route=route)
            self._buckets[bucket_id] = b

        b.route = route
        b.scope = scope
        b.observed_calls += 1
        if status == 429:
            b.observed_429 += 1

        if remaining is not None:
            b.remaining = remaining
        if limit is not None:
            b.limit = limit
        if reset_after is not None and reset_after > 0:
            b.reset_at = time.monotonic() + reset_after

        # If we hit 429 and no reset info, back off conservatively
        if status == 429 and (reset_after is None or reset_after <= 0):
            b.reset_at = time.monotonic() + _DEFAULT_RESET_AFTER
            b.remaining = 0

        # Re-point route in case bucket changed
        self._route_to_bucket[route] = bucket_id


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _get_header(headers, name: str):
    """Case-insensitive header lookup for aiohttp CIMultiDict or dict."""
    try:
        return headers.get(name)
    except AttributeError:
        return None


def _parse_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def demo():
        bm = BucketManager()
        route = "POST /unique-username/username-attempt"

        # Simulate 3 OK responses with decreasing remaining
        for rem in (4, 3, 2):
            bm.update_from_headers(route, {
                "X-RateLimit-Bucket": "abc123",
                "X-RateLimit-Remaining": str(rem),
                "X-RateLimit-Limit": "5",
                "X-RateLimit-Reset-After": "1.0",
            })
            await bm.acquire(route)
            print(f"rem={rem} → acquired OK")

        # Simulate a 429
        bm.apply_429(route, retry_after=0.5)
        t0 = time.monotonic()
        await bm.acquire(route)
        print(f"after 429: waited {time.monotonic() - t0:.2f}s")

        print(bm.snapshot())

    asyncio.run(demo())
