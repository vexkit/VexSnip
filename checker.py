#!/usr/bin/env python3
"""VexUserCheck v3.3 - Async Discord username availability checker."""

from __future__ import annotations

import asyncio.sslproto as _sslproto

_orig_eof = _sslproto.SSLProtocol.eof_received


def _safe_eof(self):
    try:
        return _orig_eof(self)
    except RuntimeError:
        return False


_sslproto.SSLProtocol.eof_received = _safe_eof

import argparse
import asyncio
import contextlib
import hashlib
import random
import sys
import time
from pathlib import Path

import aiohttp
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import (
    APP_NAME,
    APP_TAGLINE,
    BUCKET_SAFETY_MARGIN,
    DATA_DIR,
    DIRECT_MODE_JITTER,
    DIRECT_MODE_MIN_PROXIES,
    DIRECT_MODE_RATE_LIMIT,
    DIRECT_MODE_TIMEOUT,
    DIRECT_MODE_WORKERS,
    Glyph,
    HITS_FILE,
    LOGS_DIR,
    MAX_CONCURRENCY,
    PROGRESS_FILE,
    PROXY_WARMUP_CONCURRENCY,
    PROXY_WARMUP_MIN_ALIVE,
    PROXY_WARMUP_TIMEOUT,
    RESULTS_DIR,
    VERSION,
    AppSettings,
    C,
    Config,
    RunConfig,
    Stats,
    ensure_dir,
    ensure_file,
    load_lines,
)
from proxy import ProxyManager
from token_manager import TokenManager
from engine import Checker, CheckResult, CircuitBreaker, WebhookSender, set_debug, dbg
from buckets import BucketManager
from progress import ProgressTracker
from ui import (
    console,
    fail as ui_fail,
    final_summary,
    format_count,
    hr,
    info as ui_info,
    live_card,
    ok as ui_ok,
    play_hit_sound,
    warn as ui_warn,
    welcome,
)
from wizard import setup_wizard


def parse_args() -> AppSettings:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_NAME} - {APP_TAGLINE}",
    )
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-n", "--no-wizard", action="store_true")
    parser.add_argument("--resume-from-hits", action="store_true")
    parser.add_argument("--no-resume-progress", action="store_true")
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--length", type=int, default=None)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--webhook-min-hits", type=int, default=None)
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} v{VERSION}")
    args = parser.parse_args()

    return AppSettings(
        debug=args.debug,
        no_wizard=args.no_wizard,
        resume_from_hits=args.resume_from_hits,
        no_sound=args.no_sound,
        length=args.length,
        count=args.count,
        webhook_min_hits=args.webhook_min_hits,
        resume_progress=not args.no_resume_progress,
    )


async def _rps_calculator(stats: Stats, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        prev_req = stats.requests
        prev_done = stats.works + stats.taken
        await asyncio.sleep(1)
        await stats.set_rps(float(stats.requests - prev_req))
        await stats.set_checks_rps(
            float(stats.works + stats.taken - prev_done)
        )


def _load_hits_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name = line.split("\t", 1)[0].strip()
            if name:
                out.add(name)
    except Exception:
        pass
    return out


async def _shutdown_webhook(webhook, webhook_task, timeout: float = 5.0) -> None:
    if webhook is None:
        return
    with contextlib.suppress(Exception):
        webhook.stop()
    if webhook_task is not None and not webhook_task.done():
        try:
            await asyncio.wait_for(
                asyncio.shield(webhook_task), timeout=timeout
            )
        except asyncio.TimeoutError:
            webhook_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await webhook_task
        except (asyncio.CancelledError, Exception):
            pass
    with contextlib.suppress(Exception):
        await webhook.flush_all()


def _print_run_banner(proxies, tokens, usernames, mode="saved", direct=False):
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style=C.DIM, justify="right")
    t.add_column(style=C.TEXT, justify="left")
    if direct:
        t.add_row("Mode", f"[{C.SUCCESS}]DIRECT[/]")
        t.add_row("Proxies", f"[{C.MUTED}]disabled[/]")
    else:
        t.add_row("Proxies", f"[{C.BRAND}]{format_count(proxies)}[/]")
    t.add_row("Tokens", f"[{C.BRAND}]{format_count(tokens)}[/]")
    t.add_row("Usernames", f"[{C.BRAND}]{format_count(usernames)}[/]")

    console.print(Panel(
        t,
        title=f"[bold {C.BRAND}]{APP_NAME}[/] [dim]· {mode} mode[/]",
        title_align="left",
        subtitle=f"[dim]{APP_TAGLINE}[/]",
        subtitle_align="left",
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 4),
    ))
    console.print()


async def _run_checker(cfg: RunConfig, settings: AppSettings) -> None:
    skip_set: set[str] = set()
    if cfg.resume_from_hits:
        skip_set = _load_hits_names(HITS_FILE)
        if skip_set:
            ui_info(
                f"Loaded [{C.BRAND}]{format_count(len(skip_set))}[/] names"
            )

    names = [n for n in cfg.usernames if n not in skip_set]
    total = len(names)
    if total == 0:
        console.print(
            f"[{C.SUCCESS}]{Glyph.OK}[/] All names already in hits.txt."
        )
        return

    tracker = ProgressTracker(PROGRESS_FILE)
    signature = tracker.compute_signature(names)
    start_idx = 0
    if cfg.resume_progress:
        can_resume, saved_idx = tracker.can_resume(names)
        if can_resume:
            start_idx = saved_idx
            ui_ok(f"Resuming from index {format_count(saved_idx)}/{format_count(total)}")

    HITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    hits_file = HITS_FILE.open("a", encoding="utf-8")
    hits_lock = asyncio.Lock()

    # Managers
    pm = ProxyManager(cfg.proxies, remove_on_fail=cfg.remove_bad_proxies, scored=cfg.scraped)
    tm = TokenManager(cfg.tokens, remove_on_fail=cfg.remove_bad_tokens, scored=len(cfg.tokens) > 5)
    stats = Stats()
    start_time = time.time()
    bucket_manager = BucketManager()

    # ─── Direct Mode detection ───
    direct_mode = (
        not tm.is_empty
        and (not cfg.proxies or pm.alive_count < DIRECT_MODE_MIN_PROXIES)
    )
    proxyless = not cfg.proxies

    if direct_mode:
        ui_info(
            f"[bold {C.SUCCESS}]Direct mode[/] — using AUTH endpoint, "
            f"~{DIRECT_MODE_RATE_LIMIT} req/s, no proxies"
        )
        cfg.proxies = []
        cfg.concurrency = DIRECT_MODE_WORKERS
        cfg.timeout = DIRECT_MODE_TIMEOUT
        pm = ProxyManager([], remove_on_fail=False, scored=False)
        proxyless = True
    else:
        # Warm-up proxies (guarded)
        if (
            not pm.is_proxyless
            and not pm.is_single
            and hasattr(pm, "warm_up")
        ):
            ui_info(f"Warming up {format_count(pm.alive_count)} proxies...")
            before = pm.total_count
            try:
                alive, _ = await pm.warm_up(
                    timeout=PROXY_WARMUP_TIMEOUT,
                    concurrency=PROXY_WARMUP_CONCURRENCY,
                    min_alive=PROXY_WARMUP_MIN_ALIVE,
                )
                ui_ok(
                    f"Warm-up: {format_count(alive)}/{format_count(before)} ready "
                    f"(removed {format_count(before - alive)})"
                )
            except Exception as e:
                ui_warn(f"Warm-up skipped ({type(e).__name__}: {e})")

            if hasattr(pm, "start_health_loop"):
                try:
                    pm.start_health_loop(
                        interval=30.0,
                        min_alive=PROXY_WARMUP_MIN_ALIVE,
                    )
                except Exception:
                    pass
        elif not pm.is_proxyless and not pm.is_single:
            ui_info(
                f"[dim]Warm-up unavailable — using "
                f"{format_count(pm.alive_count)} proxies as-is[/]"
            )

    # Safety cap
    if cfg.concurrency > MAX_CONCURRENCY:
        cfg.concurrency = MAX_CONCURRENCY

    # FD limit
    try:
        import resource as _resource
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _resource.setrlimit(
            _resource.RLIMIT_NOFILE,
            (_hard if _hard > 1024 else 4096, _hard),
        )
    except Exception:
        pass

    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENCY * 2,
        limit_per_host=0,
        force_close=False,
        enable_cleanup_closed=True,
        ttl_dns_cache=300,
    )
    session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=30)
    session = aiohttp.ClientSession(
        connector=connector, trust_env=False, timeout=session_timeout,
    )

    # Circuit breaker
    cb: CircuitBreaker | None = None
    paused = False
    if pm.is_single and not pm.is_proxyless and not direct_mode:
        async def _on_circuit_open():
            nonlocal paused
            paused = True
            await stats.inc_circuit_open()
            await asyncio.sleep(2.0)
            paused = False
        cb = CircuitBreaker(threshold=10, window=2.0, cooldown=2.0, on_open=_on_circuit_open)

    http_timeout = DIRECT_MODE_TIMEOUT if direct_mode else (5 if proxyless else cfg.timeout)
    checker = Checker(
        pm,
        token_manager=tm if not tm.is_empty else None,
        timeout=http_timeout,
        scraped=cfg.scraped,
        direct=direct_mode,
        circuit_breaker=cb,
        stats=stats,
        bucket_manager=bucket_manager,
    )

    # Webhook
    webhook: WebhookSender | None = None
    if cfg.webhook_url and cfg.webhook_message:
        webhook = WebhookSender(
            cfg.webhook_url, cfg.webhook_message, session, start_time,
            min_hits=cfg.webhook_min_hits,
        )

    # Index
    idx_state = {"idx": start_idx}
    idx_lock = asyncio.Lock()

    async def _next_task():
        async with idx_lock:
            i = idx_state["idx"]
            if i >= total:
                return None
            idx_state["idx"] = i + 1
        return i, names[i]

    recent_hits: list[str] = []
    request_log: list[str] = []

    # Direct mode rate limiter
    direct_interval = (1.0 / DIRECT_MODE_RATE_LIMIT) if direct_mode else 0.0

    async def _worker(worker_id: int) -> None:
        while True:
            # Direct mode: rate limit
            if direct_mode and direct_interval > 0:
                jitter = random.uniform(
                    1.0 - DIRECT_MODE_JITTER,
                    1.0 + DIRECT_MODE_JITTER,
                )
                await asyncio.sleep(direct_interval * jitter)

            t = await _next_task()
            if t is None:
                return
            idx, name = t

            try:
                res: CheckResult = await checker.check(session, name)
            except Exception:
                request_log.append(f"[{C.DANGER}]{Glyph.FAIL}[/] {name}")
                continue

            r = res.result

            if r is True:
                await stats.inc_works()
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                proxy_str = res.proxy if res.proxy else "direct"
                if res.token:
                    token_hash = hashlib.sha1(
                        res.token.encode("utf-8")
                    ).hexdigest()[:8]
                else:
                    token_hash = "unauth"
                async with hits_lock:
                    with contextlib.suppress(Exception):
                        hits_file.write(
                            f"{name}\t{ts}\t{proxy_str}\t{token_hash}\n"
                        )
                        hits_file.flush()
                recent_hits.append(name)
                request_log.append(f"[{C.SUCCESS}]{Glyph.OK}[/] {name}")
                if cfg.sound_on_hit:
                    with contextlib.suppress(Exception):
                        play_hit_sound()
                if webhook:
                    webhook.enqueue(name)

            elif r is False:
                await stats.inc_taken()
                request_log.append(f"[{C.DANGER}]{Glyph.FAIL}[/] {name}")

            elif r == "EXHAUSTED":
                request_log.append(f"[{C.WARNING}]{Glyph.WARN}[/] exhausted")

            else:
                request_log.append(f"[{C.WARNING}]{Glyph.WARN}[/] {name}")

    # Background
    stop_rps = asyncio.Event()
    rps_task = asyncio.create_task(_rps_calculator(stats, stop_rps))
    webhook_task = asyncio.create_task(webhook.run()) if webhook else None

    async def _progress_saver():
        while True:
            await asyncio.sleep(5.0)
            try:
                async with idx_lock:
                    idx = idx_state["idx"]
                tracker.save(idx, total, signature)
            except Exception:
                pass

    progress_task = (
        asyncio.create_task(_progress_saver())
        if cfg.resume_progress
        else None
    )

    pending: set[asyncio.Task] = {
        asyncio.create_task(_worker(i)) for i in range(cfg.concurrency)
    }

    def _live_render():
        done = stats.works + stats.taken
        elapsed = time.time() - start_time
        if done > 0 and elapsed > 0:
            rate = done / elapsed
            eta = (total - done) / rate if rate > 0 else 0.0
        else:
            eta = 0.0

        bsnap = checker.bucket_snapshot()

        return live_card(
            done=done,
            total=total,
            works=stats.works,
            taken=stats.taken,
            requests=stats.requests,
            ratelimited=stats.ratelimited,
            circuit_opens=stats.circuit_opens,
            rps=stats.rps,
            checks_rps=stats.checks_rps,
            elapsed=elapsed,
            proxy_alive=pm.alive_count if not direct_mode else -1,
            paused=paused,
            recent=recent_hits,
            feed=request_log,
            tokens_alive=tm.alive_count if not tm.is_empty else 0,
            tokens_total=tm.total_count if not tm.is_empty else 0,
            eta_seconds=eta,
            bucket_active=bsnap.get("active", 0),
            bucket_total=bsnap.get("buckets", 0),
        )

    console.print()
    try:
        with Live(
            _live_render(),
            refresh_per_second=4,
            console=console,
            transient=False,
        ) as live:
            while pending:
                await asyncio.sleep(0.3)
                pending = {w for w in pending if not w.done()}
                async with idx_lock:
                    current_idx = idx_state["idx"]
                while current_idx < total and len(pending) < cfg.concurrency:
                    pending.add(asyncio.create_task(_worker(len(pending))))
                    async with idx_lock:
                        current_idx = idx_state["idx"]
                if not pending and current_idx >= total:
                    break
                live.update(_live_render())
    except asyncio.CancelledError:
        pass
    finally:
        stop_rps.set()
        rps_task.cancel()
        if progress_task:
            progress_task.cancel()
        for w in pending:
            w.cancel()
        if hasattr(pm, "stop_health_loop"):
            with contextlib.suppress(Exception):
                await pm.stop_health_loop()
        await _shutdown_webhook(webhook, webhook_task, timeout=5.0)
        await asyncio.sleep(0)

    try:
        async with idx_lock:
            final_idx = idx_state["idx"]
        if cfg.resume_progress:
            tracker.save(final_idx, total, signature)
    except Exception:
        pass

    elapsed = time.time() - start_time
    snap = await stats.snapshot()

    with contextlib.suppress(Exception):
        hits_file.close()
    with contextlib.suppress(Exception):
        await session.close()

    final_summary(
        requests=snap["requests"],
        works=snap["works"],
        taken=snap["taken"],
        ratelimited=snap["ratelimited"],
        circuit_opens=snap["circuit_opens"],
        elapsed=elapsed,
        peak_rps=snap["peak_rps"],
        best_streak=snap["best_streak"],
        token_401=snap["token_401"],
        tokens_alive=tm.alive_count if not tm.is_empty else 0,
        tokens_total=tm.total_count if not tm.is_empty else 0,
    )


def main() -> None:
    settings = parse_args()
    set_debug(settings.debug)

    ensure_dir(DATA_DIR, LOGS_DIR, RESULTS_DIR)
    ensure_file(DATA_DIR / "config.json")
    ensure_file(DATA_DIR / "proxies.txt")
    ensure_file(DATA_DIR / "names_to_check.txt")
    ensure_file(DATA_DIR / "tokens.txt")
    ensure_file(LOGS_DIR / "error.txt", clean=True)

    config = Config()

    try:
        if settings.no_wizard:
            welcome()

            proxy_file = config.get("last_proxy_file", str(DATA_DIR / "proxies.txt"))
            names_file = config.get("last_names_file", str(DATA_DIR / "names_to_check.txt"))
            token_file = config.get("last_token_file", str(DATA_DIR / "tokens.txt"))

            proxies = load_lines(proxy_file)
            usernames = load_lines(names_file)
            tokens = load_lines(token_file)

            if settings.length is not None:
                from wizard import _generate_usernames
                count = settings.count if settings.count is not None else 50000
                usernames = _generate_usernames(settings.length, count)
                ui_info(f"Generated {format_count(len(usernames))} usernames")

            if not proxies:
                ui_warn("No proxies found – running proxyless/direct.")
            if not usernames:
                ui_fail("No usernames found. Run without --no-wizard first.")
                sys.exit(1)
            if not tokens:
                ui_info("No tokens – using unauth endpoint.")

            webhook_min_hits = (
                settings.webhook_min_hits
                if settings.webhook_min_hits is not None
                else int(config.get("webhook_min_hits", 1))
            )

            # Auto-detect direct mode
            direct = bool(tokens) and (
                not proxies or len(proxies) < DIRECT_MODE_MIN_PROXIES
            )

            run_config = RunConfig(
                proxies=proxies,
                remove_bad_proxies=config.get("remove_proxies", False),
                usernames=usernames,
                concurrency=config.get("concurrency", 50),
                timeout=config.get("timeout", 10),
                scraped=False,
                webhook_url=config.get("webhook"),
                webhook_message=config.get("webhook_message"),
                tokens=tokens,
                remove_bad_tokens=config.get("remove_tokens", True),
                resume_from_hits=settings.resume_from_hits,
                resume_progress=settings.resume_progress,
                webhook_min_hits=max(1, int(webhook_min_hits)),
                sound_on_hit=not settings.no_sound,
                direct_mode=direct,
            )

            _print_run_banner(
                proxies=len(proxies),
                tokens=len(tokens),
                usernames=len(usernames),
                mode="saved",
                direct=direct,
            )
        else:
            run_config = asyncio.run(setup_wizard(config, settings))

        asyncio.run(_run_checker(run_config, settings))

    except (EOFError, KeyboardInterrupt):
        console.print()
        console.print(f"[{C.WARNING}]{Glyph.WARN}[/] [bold]Aborted by user.[/]")
        sys.exit(0)


if __name__ == "__main__":
    main()
