#!/usr/bin/env python3
"""VexUserCheck v3.3 - Checker engine, bucket manager, webhook sender."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from dataclasses import dataclass
from enum import Enum

import aiohttp

from config import AUTH_ENDPOINT, ENDPOINT, USER_AGENT
from buckets import BucketManager


_debug: bool = False


def set_debug(enabled: bool) -> None:
    global _debug
    _debug = enabled


def dbg(*args, **kwargs) -> None:
    if _debug:
        print(*args, file=sys.stderr, flush=True, **kwargs)


@dataclass
class CheckResult:
    result: bool | str
    data: dict | None = None
    code: int | None = None
    proxy: str | None = None
    token: str | None = None
    elapsed: float = 0.0
    attempts: int = 1


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 8,
        window: float = 2.0,
        cooldown: float = 2.0,
        half_open_attempts: int = 1,
        on_open=None,
        on_close=None,
    ) -> None:
        self.threshold = threshold
        self.window = window
        self.cooldown = cooldown
        self.half_open_attempts = half_open_attempts
        self._on_open = on_open
        self._on_close = on_close
        self._state: CircuitState = CircuitState.CLOSED
        self._failures: list[float] = []
        self._open_until: float = 0.0
        self._half_open_in_flight: int = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures.clear()
                self._half_open_in_flight = 0
                self._open_until = 0.0
                if self._on_close:
                    try:
                        asyncio.ensure_future(self._on_close())
                    except Exception:
                        pass
            elif self._state == CircuitState.CLOSED:
                self._failures.clear()

    async def record_failure(self) -> None:
        async with self._lock:
            now = time.time()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._open_until = now + self.cooldown
                self._half_open_in_flight = 0
                return
            if self._state == CircuitState.OPEN:
                self._open_until = max(
                    self._open_until, now + self.cooldown * 0.5
                )
                return
            self._failures.append(now)
            self._failures = [
                t for t in self._failures if now - t < self.window
            ]
            if len(self._failures) >= self.threshold:
                self._state = CircuitState.OPEN
                self._open_until = now + self.cooldown
                if self._on_open:
                    try:
                        asyncio.ensure_future(self._on_open())
                    except Exception:
                        pass

    async def wait_if_open(self) -> None:
        async with self._lock:
            now = time.time()
            if self._state == CircuitState.OPEN:
                if now >= self._open_until:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_in_flight = 0
                else:
                    wait = self._open_until - now
                    await asyncio.sleep(wait)
            if self._state == CircuitState.HALF_OPEN:
                while self._half_open_in_flight >= self.half_open_attempts:
                    await asyncio.sleep(0.1)
                    if self._state != CircuitState.HALF_OPEN:
                        return
                self._half_open_in_flight += 1


class ErrorAggregator:
    def __init__(
        self,
        flush_interval: float = 30.0,
        flush_count: int = 500,
        on_flush=None,
    ) -> None:
        self.flush_interval = flush_interval
        self.flush_count = flush_count
        self._on_flush = on_flush
        self._lock = asyncio.Lock()
        self._counters: dict[str, int] = {}
        self._last_flush = time.time()

    async def inc(self, category: str) -> None:
        async with self._lock:
            self._counters[category] = self._counters.get(category, 0) + 1
            total = sum(self._counters.values())
            if (
                total >= self.flush_count
                or (time.time() - self._last_flush) >= self.flush_interval
            ):
                self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._counters:
            self._last_flush = time.time()
            return
        if self._on_flush:
            try:
                self._on_flush(dict(self._counters))
            except Exception:
                pass
        self._counters.clear()
        self._last_flush = time.time()


def _parse_retry_after(
    resp: aiohttp.ClientResponse,
    body: dict | None,
) -> float:
    if body and isinstance(body, dict):
        try:
            ra = float(body.get("retry_after"))
            if ra > 0:
                return ra
        except (TypeError, ValueError):
            pass
    try:
        ra = resp.headers.get("Retry-After")
        if ra is not None:
            return max(0.1, float(ra))
    except (TypeError, ValueError):
        pass
    return 5.0


def _backoff(attempt: int, base: float = 0.3, cap: float = 5.0) -> float:
    raw = min(cap, base * (2 ** max(0, attempt - 1)))
    return raw * random.uniform(0.5, 1.5)


class Checker:
    """Async username checker with adaptive retry + circuit breaking."""

    MAX_RETRIES_STATIC = 20
    MAX_RETRIES_ROTATING = 100
    MAX_RETRIES_SCRAPED = 3
    MAX_RETRIES_DIRECT = 5  # NEW: direct mode retries
    STATIC_TOTAL_TIMEOUT = 120.0

    def __init__(
        self,
        proxy_manager,
        token_manager=None,
        timeout: int = 10,
        scraped: bool = False,
        direct: bool = False,  # NEW
        circuit_breaker: CircuitBreaker | None = None,
        stats=None,
        bucket_manager: BucketManager | None = None,
    ) -> None:
        self.pm = proxy_manager
        self.tm = token_manager
        self.timeout = timeout
        self._err = ErrorAggregator()
        self._rotating = proxy_manager.is_single
        self._scraped = scraped
        self._direct = direct
        self._cb = circuit_breaker
        self._stats = stats
        self._buckets = bucket_manager or BucketManager()

        if scraped:
            self._max_retries = self.MAX_RETRIES_SCRAPED
        elif direct:
            self._max_retries = self.MAX_RETRIES_DIRECT
        elif self._rotating:
            self._max_retries = self.MAX_RETRIES_ROTATING
        else:
            self._max_retries = self.MAX_RETRIES_STATIC

    async def check(
        self,
        session: aiohttp.ClientSession,
        username: str,
    ) -> CheckResult:
        started = time.time()
        attempt = 0
        last_proxy: str | None = None
        last_token: str | None = None

        while True:
            proxy = await self.pm.next()
            if proxy is None and not self.pm.is_proxyless and not self._direct:
                return CheckResult("EXHAUSTED", proxy=None, token=last_token)

            token: str | None = None
            if self.tm is not None and not self.tm.is_empty:
                token = await self.tm.next()

            last_proxy = proxy
            last_token = token
            attempt += 1

            if attempt > self._max_retries:
                return CheckResult(
                    "ERROR", proxy=last_proxy, token=last_token,
                    elapsed=time.time() - started,
                    attempts=attempt - 1,
                )
            if (
                not self._rotating and not self._direct
                and (time.time() - started) > self.STATIC_TOTAL_TIMEOUT
            ):
                return CheckResult(
                    "ERROR", proxy=last_proxy, token=last_token,
                    elapsed=time.time() - started,
                    attempts=attempt - 1,
                )

            if self._stats:
                await self._stats.inc_requests()

            endpoint, headers = self._build_request(token)
            route = "POST /unique-username/username-attempt"

            # Bucket acquire (only if using AUTH endpoint)
            if token and not self._direct:
                await self._buckets.acquire(route)

            t0 = time.monotonic()
            try:
                _timeout = self._pick_timeout()
                async with session.post(
                    endpoint,
                    json={"username": username},
                    proxy=proxy,
                    headers=headers,
                    timeout=_timeout,
                ) as resp:
                    if token:
                        self._buckets.update_from_headers(
                            route, resp.headers, resp.status
                        )

                    latency_ms = (time.monotonic() - t0) * 1000.0
                    dbg(f"  [{attempt}] {username} -> {resp.status}")

                    # 401
                    if resp.status == 401:
                        await self._handle_401(token)
                        continue

                    # 429
                    if resp.status == 429:
                        body = await self._safe_json(resp)
                        cooldown = _parse_retry_after(resp, body)
                        scope = resp.headers.get("X-RateLimit-Scope", "user")
                        self._buckets.apply_429(route, cooldown, scope=scope)
                        await self._handle_429(token, proxy, cooldown)
                        continue

                    # Success
                    if resp.status in (200, 201, 204):
                        body = await self._safe_json(resp)
                        taken = body.get("taken", True)
                        if self._cb:
                            await self._cb.record_success()
                        if proxy:
                            self.pm.score_hit(proxy, latency_ms=latency_ms)
                        if token and self.tm:
                            self.tm.score_hit(token)
                            self.tm.clear_cooldown(token)
                        return CheckResult(
                            not taken,
                            data=body,
                            code=resp.status,
                            proxy=proxy,
                            token=token,
                            elapsed=time.time() - started,
                            attempts=attempt,
                        )

                    # 400
                    if resp.status == 400:
                        body = await self._safe_json(resp)
                        if proxy:
                            self.pm.score_hit(proxy, latency_ms=latency_ms)
                        if token and self.tm:
                            self.tm.score_hit(token)
                        return CheckResult(
                            False,
                            data=body,
                            code=resp.status,
                            proxy=proxy,
                            token=token,
                            elapsed=time.time() - started,
                            attempts=attempt,
                        )

                    # 403
                    if resp.status == 403:
                        await self._err.inc("http_403")
                        if proxy:
                            await self.pm.mark_dead(proxy)
                        continue

                    # 5xx
                    await self._err.inc(f"http_{resp.status}")
                    await asyncio.sleep(_backoff(attempt, base=0.4, cap=3.0))
                    continue

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                dbg(f"  [{attempt}] {username} -> {type(exc).__name__}")
                if proxy:
                    self.pm.score_miss(proxy)
                if not self._scraped:
                    await self._handle_network_error(exc, proxy, attempt)
                else:
                    await asyncio.sleep(0.05)
                continue

            except Exception as exc:
                dbg(
                    f"  [{attempt}] {username} -> "
                    f"{type(exc).__name__}: {exc!s:.100}"
                )
                await asyncio.sleep(_backoff(attempt, base=0.3, cap=2.0))
                continue

    def _build_request(self, token: str | None) -> tuple[str, dict]:
        if token:
            return AUTH_ENDPOINT, {
                "Content-Type": "application/json",
                "Authorization": token,
                "User-Agent": USER_AGENT,
            }
        return ENDPOINT, {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _pick_timeout(self) -> aiohttp.ClientTimeout:
        if self._scraped:
            return aiohttp.ClientTimeout(total=4, sock_connect=3)
        if self._direct:
            return aiohttp.ClientTimeout(total=self.timeout, sock_connect=8)
        if self._rotating:
            return aiohttp.ClientTimeout(
                total=min(self.timeout, 5), sock_connect=5
            )
        return aiohttp.ClientTimeout(total=self.timeout, sock_connect=8)

    async def _safe_json(self, resp: aiohttp.ClientResponse) -> dict:
        try:
            data = await resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _handle_401(self, token: str | None) -> None:
        if token and self.tm:
            await self.tm.mark_dead(token)
            self.tm.score_miss(token)
        if self._stats:
            await self._stats.inc_token_401()
            await self._stats.inc_ratelimited()

    async def _handle_429(
        self,
        token: str | None,
        proxy: str | None,
        cooldown: float,
    ) -> None:
        if self._stats:
            await self._stats.inc_ratelimited()

        if token and self.tm:
            self.tm.set_rate_limit(token, cooldown)
            await self.tm.set_cooldown(token, cooldown)

        if self._scraped:
            if proxy:
                self.pm.set_rate_limit(proxy, cooldown)
            return

        if self._rotating:
            return

        # Direct mode or static: honor cooldown
        if proxy:
            await self.pm.set_cooldown(proxy, cooldown)
        else:
            await asyncio.sleep(cooldown)

    async def _handle_network_error(
        self,
        exc: Exception,
        proxy: str | None,
        attempt: int,
    ) -> None:
        if self._rotating:
            if self._cb:
                await self._cb.record_failure()
                await self._cb.wait_if_open()
            await asyncio.sleep(_backoff(attempt, base=0.25, cap=3.0))
            return
        await self._err.inc("proxy_conn_err")
        if proxy:
            await self.pm.set_cooldown(proxy, 3)
        await asyncio.sleep(_backoff(attempt, base=0.15, cap=5.0))

    def bucket_snapshot(self) -> dict:
        return self._buckets.snapshot()


class WebhookSender:
    DISCORD_MAX_CHARS = 1950
    INITIAL_BATCH = 15
    INITIAL_INTERVAL = 3.0
    MIN_BATCH = 5
    MAX_BATCH = 25
    MIN_INTERVAL = 2.0
    MAX_INTERVAL = 10.0

    def __init__(
        self,
        webhook_url: str,
        message_template: str,
        session: aiohttp.ClientSession,
        start_time: float = 0.0,
        min_hits: int = 1,
        bot_name: str = "VexUserCheck",
        avatar_url: str | None = None,
    ) -> None:
        self.url = webhook_url
        self.template = message_template
        self.session = session
        self.start_time = start_time
        self.min_hits = max(1, int(min_hits))
        self.bot_name = bot_name
        self.avatar_url = avatar_url or (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "9/95/Burning_Yellow_Sunset.jpg/"
            "1280px-Burning_Yellow_Sunset.jpg"
        )
        self._sent: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._buffer: list[str] = []
        self._buffer_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._dyn_batch = self.INITIAL_BATCH
        self._dyn_interval = self.INITIAL_INTERVAL
        self._rate_limit_remaining = 5
        self._sent_count = 0
        self._failed_count = 0

    def enqueue(self, username: str) -> None:
        if username in self._sent:
            return
        self._sent.add(username)
        try:
            self._queue.put_nowait(username)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        last_flush = time.time()
        while not self._stop.is_set():
            try:
                username = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                async with self._buffer_lock:
                    if self._should_flush(
                        len(self._buffer), time.time() - last_flush
                    ):
                        await self._flush_locked()
                        last_flush = time.time()
                continue
            except asyncio.CancelledError:
                break
            async with self._buffer_lock:
                self._buffer.append(username)
                if self._should_flush(
                    len(self._buffer), time.time() - last_flush
                ):
                    await self._flush_locked()
                    last_flush = time.time()
        async with self._buffer_lock:
            if self._buffer:
                await self._flush_locked()

    async def flush_all(self) -> None:
        async with self._buffer_lock:
            if self._buffer:
                await self._flush_locked()

    def _should_flush(self, count: int, since_flush: float) -> bool:
        if count == 0:
            return False
        if count < self.min_hits and since_flush < self._dyn_interval * 3:
            return False
        if count >= self._dyn_batch:
            return True
        if since_flush >= self._dyn_interval:
            return True
        return False

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        names = list(self._buffer)
        self._buffer.clear()
        msg = self._build_message(names)
        payload = {
            "content": msg,
            "username": self.bot_name,
            "avatar_url": self.avatar_url,
        }
        await self._send_with_retry(payload)

    def _build_message(self, names: list[str]) -> str:
        now = time.time()
        elapsed = int(now - self.start_time)
        ts_discord = f"<t:{int(now)}:R>"

        def _fill(n: str) -> str:
            return (
                self.template
                .replace("<name>", n)
                .replace("<t:time:R>", ts_discord)
                .replace("<time>", ts_discord)
                .replace("<elapsed>", str(elapsed))
            )

        msg = "\n".join(_fill(n) for n in names)
        if len(msg) > self.DISCORD_MAX_CHARS:
            safe = msg[: self.DISCORD_MAX_CHARS - 3]
            last_nl = safe.rfind("\n")
            msg = (
                safe[:last_nl] + "\n..."
                if last_nl > 0
                else safe + "..."
            )
        return msg

    async def _send_with_retry(
        self, payload: dict, max_tries: int = 3
    ) -> None:
        for _ in range(max_tries):
            try:
                async with self.session.post(
                    self.url, json=payload
                ) as resp:
                    self._update_rate_state(resp.headers)
                    if resp.status in (200, 204):
                        self._sent_count += 1
                        return
                    if resp.status == 429:
                        try:
                            data = await resp.json()
                            wait = float(data.get("retry_after", 5))
                        except Exception:
                            wait = 5.0
                        await asyncio.sleep(wait)
                        continue
                    self._failed_count += 1
                    return
            except Exception:
                await asyncio.sleep(1)
        self._failed_count += 1

    def _update_rate_state(self, headers) -> None:
        try:
            rem = headers.get("X-RateLimit-Remaining")
            if rem is not None:
                self._rate_limit_remaining = int(rem)
        except Exception:
            return
        if self._rate_limit_remaining <= 1:
            self._dyn_batch = max(self.MIN_BATCH, self._dyn_batch - 3)
            self._dyn_interval = min(
                self.MAX_INTERVAL, self._dyn_interval * 1.4
            )
        elif self._rate_limit_remaining >= 4:
            self._dyn_batch = min(self.MAX_BATCH, self._dyn_batch + 1)
            self._dyn_interval = max(
                self.MIN_INTERVAL, self._dyn_interval * 0.9
            )
