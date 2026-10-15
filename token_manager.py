#!/usr/bin/env python3
"""CloudChecker v3.1 – Token manager: rotation, cooldowns, scoring, validation."""

from __future__ import annotations

import asyncio
import itertools
import random
import time

import aiohttp

from config import ME_ENDPOINT, USER_AGENT, is_probable_token


class TokenManager:
    """Manages a pool of Discord tokens.

    Modes (chosen automatically by list size):
    - empty      → is_empty=True, caller uses unauth endpoint.
    - single     → like a rotating gateway: always returned.
    - multi (<=5) → round-robin with per-token cooldowns.
    - multi (>5) with scored=True → weighted random pick.
    """

    def __init__(
        self,
        tokens: list[str],
        remove_on_fail: bool = True,
        scored: bool = False,
    ) -> None:
        cleaned: list[str] = []
        for t in tokens:
            if not t:
                continue
            t = t.strip()
            if not t or t.startswith("#"):
                continue
            if is_probable_token(t):
                cleaned.append(t)

        self._tokens: list[str] = cleaned
        self._cycle = itertools.cycle(self._tokens) if self._tokens else None
        self._dead: set[str] = set()
        self._cooldowns: dict[str, float] = {}
        self.remove_on_fail = remove_on_fail
        self._lock = asyncio.Lock()

        # ── Scoring (for large pools) ──
        self._scored = scored and len(self._tokens) > 0
        self._scores: dict[str, int] = {}
        self._rate_limited_until: dict[str, float] = {}
        if self._scored:
            for tok in self._tokens:
                self._scores[tok] = 1

    # ── Properties ──

    @property
    def is_empty(self) -> bool:
        return not self._tokens

    @property
    def is_single(self) -> bool:
        return len(self._tokens) == 1

    @property
    def alive_count(self) -> int:
        return max(0, len(self._tokens) - len(self._dead))

    @property
    def total_count(self) -> int:
        return len(self._tokens)

    @property
    def dead_tokens(self) -> list[str]:
        return list(self._dead)

    # ── Core: next token ──

    async def next(self) -> str | None:
        if self.is_empty:
            return None
        if self.is_single:
            return self._tokens[0]
        if self._scored:
            return await self._next_scored()
        return await self._next_roundrobin()

    async def _next_scored(self) -> str | None:
        async with self._lock:
            now = time.time()
            live: dict[str, int] = {}
            for t, s in self._scores.items():
                if s <= 0 or t in self._dead:
                    continue
                if self._cooldowns.get(t, 0) > now:
                    continue
                if self._rate_limited_until.get(t, 0) > now:
                    continue
                live[t] = s

            if not live:
                # Fallback: ignore rate-limit cooldowns, use any non-dead
                for t, s in self._scores.items():
                    if s > 0 and t not in self._dead:
                        live[t] = s
                if not live:
                    return None

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

    async def _next_roundrobin(self) -> str | None:
        while True:
            async with self._lock:
                if len(self._dead) >= len(self._tokens):
                    return None
                for _ in range(len(self._tokens) * 2):
                    tok = next(self._cycle)
                    if tok in self._dead:
                        continue
                    if self._cooldowns.get(tok, 0) > time.time():
                        continue
                    return tok
                now = time.time()
                future = [v for v in self._cooldowns.values() if v > now]
                if not future:
                    return None
                wait = max(min(future) - now, 0.2)
            await asyncio.sleep(wait)

    # ── Scoring API ──

    def score_hit(self, token: str | None) -> None:
        if not self._scored or not token:
            return
        if token in self._scores:
            self._scores[token] = min(self._scores[token] + 5, 100)

    def score_miss(self, token: str | None) -> None:
        if not self._scored or not token:
            return
        if token in self._scores:
            self._scores[token] -= 1
            if self._scores[token] <= 0:
                self._dead.add(token)

    def set_rate_limit(self, token: str | None, seconds: float) -> None:
        if not self._scored or not token:
            return
        if token in self._scores:
            self._rate_limited_until[token] = time.time() + seconds

    # ── Mutation API ──

    async def mark_dead(self, token: str | None) -> None:
        if not self.remove_on_fail or not token:
            return
        async with self._lock:
            self._dead.add(token)

    async def set_cooldown(self, token: str | None, seconds: float) -> None:
        if not token or seconds <= 0:
            return
        async with self._lock:
            self._cooldowns[token] = time.time() + seconds

    def clear_cooldown(self, token: str | None) -> None:
        if not token:
            return
        self._cooldowns.pop(token, None)

    # ── Validation ──

    @staticmethod
    async def validate_one(token: str, timeout: float = 10.0) -> tuple[bool, str]:
        headers = {"Authorization": token, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                trust_env=False,
            ) as sess:
                async with sess.get(ME_ENDPOINT, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return True, data.get("username", "unknown")
                    if resp.status == 401:
                        return False, "invalid (401)"
                    if resp.status == 403:
                        return False, "forbidden (403)"
                    return False, f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception as e:
            return False, type(e).__name__

    async def validate_all(
        self,
        concurrency: int = 10,
        progress_cb=None,
    ) -> tuple[int, int]:
        if self.is_empty:
            return 0, 0

        tokens = list(self._tokens)
        total = len(tokens)
        sem = asyncio.Semaphore(max(1, concurrency))
        counter = {"done": 0}
        lock = asyncio.Lock()

        async def _one(tok: str):
            async with sem:
                ok, info = await self.validate_one(tok)
            if not ok and self.remove_on_fail:
                async with self._lock:
                    self._dead.add(tok)
            async with lock:
                counter["done"] += 1
                done = counter["done"]
            if progress_cb:
                try:
                    await progress_cb(done, total, ok, info)
                except Exception:
                    pass
            return tok, ok, info

        results = await asyncio.gather(*[_one(t) for t in tokens])
        working = sum(1 for _, ok, _ in results if ok)
        return working, total