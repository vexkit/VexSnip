#!/usr/bin/env python3
"""VexUserCheck v3.1 - Proxy manager with rotation, cooldowns, and scoring.

Design principles:
  · Normalize-first: every proxy becomes a canonical URL once.
  · Dedup by host:port, not by raw string.
  · Scoring starts at PROXY_START_SCORE (3), so a proxy survives a few
    transient failures before being marked dead.
  · Scored mode falls back cleanly to static round-robin when needed.
  · Fully async, thread-safe, and observable via snapshot().
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import aiohttp

from config import (
    ENDPOINT,
    USER_AGENT,
    PROXY_START_SCORE,
    PROXY_SCORE_HIT,
    PROXY_SCORE_MISS,
    PROXY_MAX_SCORE,
    PROXY_VALIDATE_TIMEOUT,
    PROXY_VALIDATE_CONCURRENCY,
)


# ============================================================================
# CONSTANTS
# ============================================================================

_ALLOWED_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks5h"})
_MIN_PORT = 1
_MAX_PORT = 65535


# ============================================================================
# PER-PROXY STATS
# ============================================================================

@dataclass
class ProxyStats:
    hits: int = 0
    misses: int = 0
    rate_limits: int = 0
    dead_marks: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def success_rate(self) -> float:
        t = self.total
        return (self.hits / t * 100.0) if t else 0.0


# ============================================================================
# HELPERS
# ============================================================================

def normalize_proxy(raw: str | None) -> str | None:
    """Return a canonical proxy URL, or None if invalid."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    # Strip surrounding quotes
    raw = raw.strip('"').strip("'")

    # Extract ip:port from JSON-ish fragments
    if raw.startswith("{"):
        import re
        m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})", raw)
        if not m:
            return None
        raw = f"{m.group(1)}:{m.group(2)}"

    if "://" not in raw:
        raw = "http://" + raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None

    hostname = parsed.hostname
    port = parsed.port

    if not hostname or not port:
        return None

    if not (_MIN_PORT <= port <= _MAX_PORT):
        return None

    return raw


def proxy_key(url: str) -> str:
    """Return the 'host:port' dedup key for a canonical proxy URL."""
    try:
        p = urlparse(url)
        host = p.hostname or ""
        port = p.port or 0
        return f"{host}:{port}"
    except Exception:
        return url


def is_valid_proxy(raw: str | None) -> bool:
    return normalize_proxy(raw) is not None


# ============================================================================
# PROXY MANAGER
# ============================================================================

class ProxyManager:
    """Manages proxy rotation with cooldowns and score-based selection."""

    def __init__(
        self,
        proxies: Iterable[str] | None,
        remove_on_fail: bool = True,
        scored: bool = False,
    ) -> None:
        # Normalize + dedup
        raw_list = list(proxies) if proxies else []
        seen_keys: set[str] = set()
        canonical: list[str] = []

        for p in raw_list:
            n = normalize_proxy(p)
            if not n:
                continue
            k = proxy_key(n)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            canonical.append(n)

        self._proxies: list[str] = canonical
        self._proxyless = not self._proxies

        self._cycle = itertools.cycle(self._proxies) if self._proxies else None
        self._dead: set[str] = set()
        self._cooldowns: dict[str, float] = {}
        self._rate_limited_until: dict[str, float] = {}
        self.remove_on_fail = remove_on_fail

        # Locks
        self._lock = asyncio.Lock()
        self._score_lock = asyncio.Lock()

        # Scoring
        self._scored = scored and not self._proxyless
        self._scores: dict[str, int] = {}
        self._stats: dict[str, ProxyStats] = {}

        if self._scored:
            for url in self._proxies:
                self._scores[url] = PROXY_START_SCORE
                self._stats[url] = ProxyStats()

    # Properties

    @property
    def is_proxyless(self) -> bool:
        return self._proxyless

    @property
    def is_single(self) -> bool:
        return len(self._proxies) == 1 and not self._proxyless

    @property
    def alive_count(self) -> int:
        if self.is_single:
            return 1
        return max(0, len(self._proxies) - len(self._dead))

    @property
    def dead_count(self) -> int:
        return len(self._dead)

    @property
    def cooldown_count(self) -> int:
        now = time.time()
        return sum(1 for v in self._cooldowns.values() if v > now)

    @property
    def scored_count(self) -> int:
        return len(self._scores) if self._scored else 0

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    @property
    def all_proxies(self) -> list[str]:
        return list(self._proxies)

    # Core: next proxy

    async def next(self) -> str | None:
        if self._proxyless:
            return None
        if self.is_single:
            return self._proxies[0]
        if self._scored:
            return await self._next_scored()
        return await self._next_static()

    async def _next_scored(self) -> str | None:
        async with self._score_lock:
            now = time.time()
            live: dict[str, int] = {}

            for url, score in self._scores.items():
                if score <= 0 or url in self._dead:
                    continue
                if self._cooldowns.get(url, 0) > now:
                    continue
                if self._rate_limited_until.get(url, 0) > now:
                    continue
                live[url] = score

            if not live:
                # Relax cooldowns first
                for url, score in self._scores.items():
                    if score > 0 and url not in self._dead:
                        live[url] = score

            if live:
                keys = list(live.keys())
                weights = [live[k] for k in keys]
                total_w = sum(weights)
                if total_w <= 0:
                    return random.choice(keys)
                pick = random.uniform(0, total_w)
                acc = 0.0
                for k, w in zip(keys, weights):
                    acc += w
                    if acc >= pick:
                        return k
                return keys[-1]

        # No scored proxies available → static round-robin fallback
        return await self._next_static()

    async def _next_static(self) -> str | None:
        if not self._proxies:
            return None

        while True:
            async with self._lock:
                if len(self._dead) >= len(self._proxies):
                    return None

                for _ in range(len(self._proxies) * 2):
                    url = next(self._cycle)
                    if url in self._dead:
                        continue
                    if self._cooldowns.get(url, 0) > time.time():
                        continue
                    return url

                now = time.time()
                future = [v for v in self._cooldowns.values() if v > now]
                if not future:
                    return None
                wait = max(min(future) - now, 0.2)

            await asyncio.sleep(wait)

    # Scoring API

    def score_hit(self, proxy: str | None) -> None:
        if not proxy:
            return
        st = self._stats.get(proxy)
        if st:
            st.hits += 1
        if self._scored and proxy in self._scores:
            self._scores[proxy] = min(
                self._scores[proxy] + PROXY_SCORE_HIT, PROXY_MAX_SCORE
            )

    def score_miss(self, proxy: str | None) -> None:
        if not proxy:
            return
        st = self._stats.get(proxy)
        if st:
            st.misses += 1
        if self._scored and proxy in self._scores:
            self._scores[proxy] -= PROXY_SCORE_MISS
            if self._scores[proxy] <= 0:
                self._dead.add(proxy)

    def set_rate_limit(self, proxy: str | None, seconds: float) -> None:
        if not proxy:
            return
        st = self._stats.get(proxy)
        if st:
            st.rate_limits += 1
        if seconds > 0:
            self._rate_limited_until[proxy] = time.time() + seconds

    # Mutation

    async def mark_dead(self, proxy: str | None) -> None:
        if not self.remove_on_fail or proxy is None:
            return
        async with self._lock:
            self._dead.add(proxy)
        st = self._stats.get(proxy)
        if st:
            st.dead_marks += 1

    async def set_cooldown(self, proxy: str | None, seconds: float) -> None:
        if proxy is None or seconds <= 0:
            return
        async with self._lock:
            self._cooldowns[proxy] = time.time() + seconds

    async def reset_dead(self) -> int:
        async with self._lock:
            n = len(self._dead)
            self._dead.clear()
        async with self._score_lock:
            for k in self._scores:
                if self._scores[k] <= 0:
                    self._scores[k] = PROXY_START_SCORE
        return n

    # Observability

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "total": self.total_count,
            "alive": self.alive_count,
            "dead": self.dead_count,
            "cooldown": self.cooldown_count,
            "scored": self.scored_count,
            "mode": (
                "proxyless" if self._proxyless
                else "single" if self.is_single
                else "scored" if self._scored
                else "static"
            ),
            "rate_limited": sum(
                1 for v in self._rate_limited_until.values() if v > now
            ),
        }

    def describe(self) -> str:
        if self._proxyless:
            return "proxyless"
        if self.is_single:
            return "1 gateway"
        if self._scored:
            return f"{self.alive_count}/{self.total_count} scored"
        return f"{self.alive_count}/{self.total_count} static"

    def __repr__(self) -> str:
        return f"<ProxyManager {self.describe()}>"

    # Validation

    async def validate(
        self,
        timeout: float | None = None,
        concurrency: int | None = None,
        sample: int | None = None,
        progress_cb=None,
    ) -> tuple[int, int]:
        """Test proxies concurrently. Returns (working, total)."""
        timeout = timeout if timeout is not None else PROXY_VALIDATE_TIMEOUT
        concurrency = concurrency or PROXY_VALIDATE_CONCURRENCY

        async def _test_one(proxy_url: str) -> bool:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    trust_env=False,
                ) as sess:
                    async with sess.post(
                        ENDPOINT,
                        json={"username": "a"},
                        proxy=proxy_url,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": USER_AGENT,
                        },
                    ) as resp:
                        return resp.status in (200, 201, 204, 400)
            except Exception:
                return False

        pool = list(self._proxies)
        if sample is not None:
            pool = random.sample(pool, min(sample, len(pool)))
        if not pool:
            return 0, 0

        sem = asyncio.Semaphore(max(1, concurrency))
        state = {"done": 0}
        state_lock = asyncio.Lock()
        total = len(pool)

        async def _worker(url: str) -> bool:
            async with sem:
                ok = await _test_one(url)
            async with state_lock:
                state["done"] += 1
                done = state["done"]
            if progress_cb:
                try:
                    await progress_cb(done, total, ok)
                except Exception:
                    pass
            return ok

        results = await asyncio.gather(*[_worker(u) for u in pool])
        return sum(results), total