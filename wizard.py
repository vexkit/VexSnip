#!/usr/bin/env python3
"""VexUserCheck v3.3 - Setup wizard and proxy scraper.

Five steps:
  1. Proxies   -> file / paste / scrape / none
  2. Tokens    -> file / paste / none (with validation)
  3. Usernames -> file / generate (with quality filter)
  4. Speed     -> concurrency + timeout (auto-suggests Direct Mode)
  5. Webhook   -> URL + template + min_hits (+ resume options)

Proxy sources: 36 verified endpoints, capped at MAX_PROXIES_CAP after
dedup, with auto-validation for lists above PROXY_VALIDATE_THRESHOLD.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from rich.live import Live
from rich.prompt import Confirm, IntPrompt, Prompt

from config import (
    DATA_DIR,
    DIRECT_MODE_MIN_PROXIES,
    DIRECT_MODE_TIMEOUT,
    DIRECT_MODE_WORKERS,
    Glyph,
    MAX_CONCURRENCY,
    MAX_PROXIES_CAP,
    PROXY_VALIDATE_THRESHOLD,
    PROXY_VALIDATE_TIMEOUT,
    PROJECT_ROOT,
    QUALITY_MIN_SCORE,
    USERNAME_CHARS,
    AppSettings,
    Config,
    RunConfig,
    ensure_file,
    is_probable_token,
    is_valid_username,
    load_lines,
)
from token_manager import TokenManager
from ui import (
    C,
    config_summary,
    console,
    fail,
    format_count,
    hr,
    info,
    info_card,
    ok,
    print_step,
    progress_steps,
    proxy_validation_card,
    quality_card,
    token_validation_card,
    warn,
    warn_card,
    welcome,
)

# Paths
DEFAULT_PROXY_FILE = str(DATA_DIR / "proxies.txt")
DEFAULT_NAMES_FILE = str(DATA_DIR / "names_to_check.txt")
DEFAULT_TOKEN_FILE = str(DATA_DIR / "tokens.txt")

_PROXY_FILE_DISPLAY = "data/proxies.txt"
_NAMES_FILE_DISPLAY = "data/names_to_check.txt"
_TOKEN_FILE_DISPLAY = "data/tokens.txt"

# Regex
_PROXY_RE = re.compile(
    r"^(?:https?://)?(?:[^@\s]+@)?"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-.]*[a-zA-Z0-9])?:\d{1,5}$"
)


# ============================================================================
# PROXY SOURCES
# ============================================================================

@dataclass(frozen=True)
class ProxySource:
    name: str
    url: str
    protocol: str = "http"


PROXY_SOURCES: list[ProxySource] = [
    # Tier 1
    ProxySource("TheSpeedX", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ProxySource("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ProxySource("proxifly", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
    ProxySource("ShiftyTR-http", "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt"),
    ProxySource("ShiftyTR-https", "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt"),
    ProxySource("roosterkid-https", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt"),
    ProxySource("roosterkid-http", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTP_RAW.txt"),

    # Tier 2
    ProxySource("KangProxy", "https://cdn.jsdelivr.net/gh/officialputuid/KangProxy@main/http/http.txt"),
    ProxySource("KangProxy-tested", "https://cdn.jsdelivr.net/gh/officialputuid/KangProxy@main/xResults/RAW.txt"),
    ProxySource("ProxyScrape-CDN", "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/all/data.txt"),
    ProxySource("databay-labs", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt"),
    ProxySource("databay-https", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/https.txt"),

    # Tier 3
    ProxySource("TuanMinPay-all", "https://raw.githubusercontent.com/tuanminpay/live-proxy/master/all.txt"),
    ProxySource("TuanMinPay-http", "https://raw.githubusercontent.com/tuanminpay/live-proxy/master/http.txt"),
    ProxySource("stormsia-working", "https://raw.githubusercontent.com/stormsia/proxy-list/main/working_proxies.txt"),
    ProxySource("stormsia-http", "https://raw.githubusercontent.com/stormsia/proxy-list/main/http.txt"),
    ProxySource("Skillter-http", "https://raw.githubusercontent.com/Skillter/ProxyGather/refs/heads/master/proxies/working-proxies-http.txt"),
    ProxySource("theriturajps", "https://raw.githubusercontent.com/theriturajps/proxy-list/main/proxies.txt"),
    ProxySource("gfpcom", "https://raw.githubusercontent.com/gfpcom/free-proxy-list/main/list/http.txt"),
    ProxySource("proxy-free-http", "https://raw.githubusercontent.com/proxy-free/free-proxy-list/main/http.txt"),
    ProxySource("Thordata-all", "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/all.txt"),
    ProxySource("Thordata-top", "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/top-trusted.txt"),

    # Tier 4
    ProxySource("sunny9577", "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt"),
    ProxySource("rdavydov", "https://raw.githubusercontent.com/rdavydov/proxy-list/main/proxies/http.txt"),
    ProxySource("Anonym0usWork12", "https://raw.githubusercontent.com/Anonym0usWork12/Proxy-List/master/http.txt"),
    ProxySource("officialputuid", "https://raw.githubusercontent.com/officialputuid/rules/master/proxies.txt"),
    ProxySource("mmpx12-http", "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt"),
    ProxySource("mmpx12-https", "https://raw.githubusercontent.com/mmpx12/proxy-list/master/https.txt"),
    ProxySource("iplocate-http", "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt"),
    ProxySource("iplocate-https", "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/https.txt"),
    ProxySource("zloi-user", "https://raw.githubusercontent.com/zloi-user/hideip.me/refs/heads/main/http.txt"),
    ProxySource("dinoz0rg", "https://raw.githubusercontent.com/dinoz0rg/proxy-list/main/scraped_proxies/http.txt"),

    # Tier 5
    ProxySource("openproxylist.xyz", "https://api.openproxylist.xyz/http.txt"),
    ProxySource("proxyscrape-api", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
    ProxySource("geonode-api", "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps"),
    ProxySource("ProxyScrape-github", "https://raw.githubusercontent.com/ProxyScrape/free-proxy-list/main/proxies/all/data.txt"),
]


# ============================================================================
# HELPERS
# ============================================================================

def _resolve_input_path(raw: str) -> str:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def _clean_proxy_line(line: str) -> str | None:
    """Extract a canonical proxy from an arbitrary text line."""
    line = line.strip().strip('"').strip("'")
    if not line:
        return None
    m = re.search(
        r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})",
        line,
    )
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    if _PROXY_RE.match(line):
        return line
    return None


# ============================================================================
# SETUP WIZARD
# ============================================================================

async def setup_wizard(config: Config, settings: AppSettings) -> RunConfig:
    welcome()
    console.print(progress_steps(0), justify="center")
    console.print()

    # ── Step 1: Proxies ──
    print_step(1, 5, "Proxies")
    warn_card(
        f"[{C.WARNING}]{Glyph.WARN} Proxies strongly recommended[/]",
        f"[{C.MUTED}]Without them Discord will rate-limit you.[/]",
        f"[{C.MUTED}]Format: login:pass@host:port[/]",
    )
    proxies, remove_bad, scraped = await _step_proxies(config)

    # ── Step 2: Tokens ──
    console.print()
    print_step(2, 5, "Tokens")
    tokens, remove_bad_tokens, token_working = await _step_tokens(config)

    # ── Step 3: Usernames ──
    console.print()
    print_step(3, 5, "Usernames")
    usernames = _step_usernames(settings)

    # ── Step 4: Speed (with Direct Mode auto-detect) ──
    console.print()
    print_step(4, 5, "Speed")
    concurrency, timeout, direct_mode = _step_speed(
        proxies,
        scraped=scraped,
        has_token=len(tokens) > 0,
    )

    # ── Step 5: Webhook + Resume ──
    console.print()
    print_step(5, 5, "Webhook & Options")
    webhook_url, webhook_msg, webhook_min_hits = _step_webhook(config)
    resume_from_hits = _step_resume_options(config, settings)

    # ── Persist choices ──
    config.set("timeout", timeout)
    config.set("concurrency", concurrency)
    config.set("remove_proxies", remove_bad)
    config.set("remove_tokens", remove_bad_tokens)

    # ── Summary ──
    config_summary(
        proxy_count=len(proxies),
        scraped=scraped,
        remove_bad=remove_bad,
        username_count=len(usernames),
        concurrency=concurrency,
        timeout=timeout,
        webhook=bool(webhook_url),
        token_count=len(tokens),
        token_working=token_working,
        resume_from_hits=resume_from_hits,
        resume_progress=settings.resume_progress,
        direct_mode=direct_mode,
    )

    hr()

    if not Confirm.ask(
        f"[{C.BRAND}]{Glyph.ROCKET} Start checking?[/]",
        default=True,
    ):
        console.print(f"[{C.MUTED}]Aborted.[/]")
        raise SystemExit(0)

    return RunConfig(
        proxies=proxies,
        remove_bad_proxies=remove_bad,
        usernames=usernames,
        concurrency=concurrency,
        timeout=timeout,
        scraped=scraped,
        webhook_url=webhook_url,
        webhook_message=webhook_msg,
        tokens=tokens,
        remove_bad_tokens=remove_bad_tokens,
        resume_from_hits=resume_from_hits,
        resume_progress=settings.resume_progress,
        webhook_min_hits=webhook_min_hits,
        sound_on_hit=not settings.no_sound,
        direct_mode=direct_mode,
    )


# ============================================================================
# STEP 1: PROXIES
# ============================================================================

async def _step_proxies(config: Config) -> tuple[list[str], bool, bool]:
    proxies: list[str] = []
    remove_bad = False
    scraped = False

    existing = load_lines(DEFAULT_PROXY_FILE)
    reuse_cfg = config.get("reuse_proxies")

    if existing and reuse_cfg is None:
        console.print()
        info_card(
            f"[{C.BRAND}]{Glyph.INFO}[/] Found "
            f"[{C.BRAND}]{format_count(len(existing))}[/] proxies "
            f"from last session."
        )
        if Confirm.ask(f"[{C.BRAND}]Reuse them?[/]", default=False):
            proxies = existing
            ok(f"Reusing {format_count(len(proxies))} proxies")
            if Confirm.ask(
                f"[{C.MUTED}]Always reuse without asking?[/]",
                default=False,
            ):
                config.set("reuse_proxies", True)
        else:
            if Confirm.ask(
                f"[{C.MUTED}]Always skip and ask for new ones?[/]",
                default=False,
            ):
                config.set("reuse_proxies", False)
    elif existing and reuse_cfg:
        proxies = existing
        ok(f"Auto-reusing {format_count(len(proxies))} proxies")

    if not proxies:
        console.print()
        mode = Prompt.ask(
            f"[{C.BRAND}]Proxy source[/] "
            f"[dim](f)ile . (p)aste . (s)crape . (n)one[/]",
            choices=["f", "p", "s", "n"],
            default="f",
        )

        if mode == "f":
            proxy_path = Prompt.ask(
                f"[{C.BRAND}]Path to proxy file[/]",
                default=_PROXY_FILE_DISPLAY,
            )
            proxies = load_lines(_resolve_input_path(proxy_path))
            if not proxies:
                fail("No proxies found in that file.")
        elif mode == "p":
            console.print()
            console.print(
                f"[{C.BRAND}]Paste your proxies[/] "
                f"[dim](one per line, empty line to finish)[/]"
            )
            console.print(f"[{C.MUTED}]Format: login:pass@host:port[/]")
            lines_pasted: list[str] = []
            while True:
                line = Prompt.ask(f"[{C.BRAND}]>[/]", default="")
                if not line.strip():
                    break
                lines_pasted.append(line.strip())
            if lines_pasted:
                ensure_file(DEFAULT_PROXY_FILE)
                Path(DEFAULT_PROXY_FILE).write_text(
                    "\n".join(lines_pasted), encoding="utf-8"
                )
                proxies = lines_pasted
            else:
                fail("No proxies provided.")
        elif mode == "s":
            console.print()
            scraped_proxies = await _scrape_proxies()
            if scraped_proxies:
                proxies = scraped_proxies
                scraped = True

    if proxies:
        tag = f" [{C.MUTED}](free)[/]" if scraped else ""
        ok(
            f"[{C.BRAND}]{format_count(len(proxies))}[/] "
            f"proxies loaded{tag}"
        )

        # Auto-validate large lists
        should_validate = False
        if len(proxies) >= PROXY_VALIDATE_THRESHOLD:
            should_validate = True
            console.print(
                f"  [{C.MUTED}]{Glyph.INFO}[/] "
                f"{format_count(len(proxies))} proxies "
                f"[dim](validation required for lists over "
                f"{format_count(PROXY_VALIDATE_THRESHOLD)})[/dim]"
            )
        elif len(proxies) >= 2:
            should_validate = Confirm.ask(
                f"[{C.BRAND}]Validate proxies before starting?[/] "
                f"[dim](takes ~30s)[/]",
                default=False,
            )

        if should_validate:
            before = len(proxies)
            proxies = await _validate_proxies_interactive(proxies)
            after = len(proxies)

            if not proxies:
                warn_card(
                    f"[{C.WARNING}]{Glyph.WARN} All proxies failed "
                    f"- running proxyless.[/]"
                )
                return [], True, scraped

            ok(
                f"[{C.SUCCESS}]{format_count(after)}[/] working "
                f"proxies "
                f"[{C.MUTED}](filtered from "
                f"{format_count(before)})[/]"
            )

            # Persist validated proxies
            try:
                Path(DEFAULT_PROXY_FILE).write_text(
                    "\n".join(proxies), encoding="utf-8"
                )
                info(
                    f"Saved {format_count(after)} valid proxies "
                    f"to {_PROXY_FILE_DISPLAY}"
                )
            except Exception:
                pass

        if len(proxies) > 1:
            remove_bad = Confirm.ask(
                f"[{C.BRAND}]Auto-remove dead proxies?[/]",
                default=True,
            )
    else:
        warn_card(
            f"[{C.WARNING}]{Glyph.WARN} Running proxyless "
            f"- expect heavy rate-limiting.[/]",
            f"[{C.MUTED}]Tip: close your Discord client to free up "
            f"requests.[/]",
        )

    return proxies, remove_bad, scraped


async def _validate_proxies_interactive(proxies: list[str]) -> list[str]:
    """Live-card proxy validation. Returns the working subset.

    Uses a clean stop-event instead of task cancellation to avoid
    asyncio.CancelledError leaking out of the finally block.
    """
    from proxy import normalize_proxy, proxy_key

    # Pre-normalize + dedup to avoid wasting validation slots
    seen: set[str] = set()
    clean: list[str] = []
    for raw in proxies:
        n = normalize_proxy(raw)
        if not n:
            continue
        k = proxy_key(n)
        if k in seen:
            continue
        seen.add(k)
        clean.append(n)

    if not clean:
        return []

    total = len(clean)
    state = {"done": 0, "working": 0, "dead": 0}
    results: dict[str, bool] = {}

    sem = asyncio.Semaphore(150)

    async def _one(url: str) -> None:
        ok_flag = False
        async with sem:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=PROXY_VALIDATE_TIMEOUT,
                        sock_connect=PROXY_VALIDATE_TIMEOUT,
                    ),
                    trust_env=False,
                ) as sess:
                    async with sess.post(
                        "https://discord.com/api/v9/"
                        "unique-username/username-attempt-unauthed",
                        json={"username": "a"},
                        proxy=url,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        ok_flag = resp.status in (200, 201, 204, 400)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                ok_flag = False

        state["done"] += 1
        if ok_flag:
            state["working"] += 1
        else:
            state["dead"] += 1
        results[url] = ok_flag

    with Live(
        proxy_validation_card(0, total, 0, 0),
        refresh_per_second=8,
        console=console,
        transient=True,
    ) as live:
        stop = asyncio.Event()

        async def _updater() -> None:
            while not stop.is_set():
                live.update(
                    proxy_validation_card(
                        state["done"],
                        total,
                        state["working"],
                        state["dead"],
                    )
                )
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=0.15)

        updater = asyncio.create_task(_updater())

        try:
            await asyncio.gather(*[_one(u) for u in clean])
        finally:
            stop.set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(updater, timeout=1.0)
            with contextlib.suppress(Exception):
                live.update(
                    proxy_validation_card(
                        state["done"],
                        total,
                        state["working"],
                        state["dead"],
                    )
                )

    return [u for u in clean if results.get(u, False)]


# ============================================================================
# STEP 2: TOKENS
# ============================================================================

async def _step_tokens(config: Config) -> tuple[list[str], bool, int]:
    tokens: list[str] = []
    remove_bad = True
    working = 0

    console.print()
    info_card(
        f"[{C.MUTED}]Tokens are optional. Without them, we use the "
        f"unauth endpoint.[/]",
        f"[{C.MUTED}]With tokens: higher rate limits, more reliable "
        f"results.[/]",
        f"[{C.MUTED}]Format: one token per line in data/tokens.txt[/]",
    )

    existing = load_lines(DEFAULT_TOKEN_FILE)
    reuse_cfg = config.get("reuse_tokens")

    if existing and reuse_cfg is None:
        console.print()
        info_card(
            f"[{C.BRAND}]{Glyph.INFO}[/] Found "
            f"[{C.BRAND}]{format_count(len(existing))}[/] tokens "
            f"from last session."
        )
        if Confirm.ask(f"[{C.BRAND}]Reuse them?[/]", default=True):
            tokens = existing
            ok(f"Reusing {format_count(len(tokens))} tokens")
            if Confirm.ask(
                f"[{C.MUTED}]Always reuse without asking?[/]",
                default=False,
            ):
                config.set("reuse_tokens", True)
        else:
            if Confirm.ask(
                f"[{C.MUTED}]Always skip and ask for new ones?[/]",
                default=False,
            ):
                config.set("reuse_tokens", False)
    elif existing and reuse_cfg:
        tokens = existing
        ok(f"Auto-reusing {format_count(len(tokens))} tokens")

    if not tokens:
        console.print()
        mode = Prompt.ask(
            f"[{C.BRAND}]Token source[/] "
            f"[dim](f)ile . (p)aste . (n)one[/]",
            choices=["f", "p", "n"],
            default="n",
        )

        if mode == "f":
            token_path = Prompt.ask(
                f"[{C.BRAND}]Path to token file[/]",
                default=_TOKEN_FILE_DISPLAY,
            )
            raw = load_lines(_resolve_input_path(token_path))
            tokens = [t for t in raw if is_probable_token(t)]
            bad = len(raw) - len(tokens)
            if bad > 0:
                warn(f"{bad} token(s) failed format check - skipped.")
        elif mode == "p":
            console.print()
            console.print(
                f"[{C.BRAND}]Paste your tokens[/] "
                f"[dim](one per line, empty line to finish)[/]"
            )
            lines_pasted: list[str] = []
            while True:
                line = Prompt.ask(f"[{C.BRAND}]>[/]", default="")
                if not line.strip():
                    break
                if is_probable_token(line.strip()):
                    lines_pasted.append(line.strip())
                else:
                    warn(
                        f"Skipped malformed token "
                        f"(len={len(line.strip())})"
                    )
            if lines_pasted:
                ensure_file(DEFAULT_TOKEN_FILE)
                Path(DEFAULT_TOKEN_FILE).write_text(
                    "\n".join(lines_pasted), encoding="utf-8"
                )
                tokens = lines_pasted

    if not tokens:
        info(
            "No tokens - will use unauth endpoint (lower rate "
            "limits)."
        )
        return [], True, 0

    ok(f"{format_count(len(tokens))} token(s) loaded. Validating...")
    if len(tokens) > 1:
        remove_bad = Confirm.ask(
            f"[{C.BRAND}]Auto-remove invalid tokens?[/]",
            default=True,
        )

    tm = TokenManager(tokens, remove_on_fail=False)
    total = tm.total_count
    state = {"working": 0, "dead": 0, "done": 0}

    with Live(
        token_validation_card(0, total, 0, 0),
        refresh_per_second=8,
        console=console,
        transient=True,
    ) as live:
        async def _cb(
            done: int, tot: int, ok_flag: bool, info_str: str
        ) -> None:
            state["done"] = done
            if ok_flag:
                state["working"] += 1
            else:
                state["dead"] += 1
            live.update(
                token_validation_card(
                    done, tot, state["working"], state["dead"]
                )
            )

        with contextlib.suppress(Exception):
            working, total = await tm.validate_all(
                concurrency=10, progress_cb=_cb
            )

    console.print()
    if working > 0:
        ok(
            f"[{C.SUCCESS}]{working}[/]/{total} tokens valid "
            f"- will use [bold]AUTH[/] endpoint."
        )
    else:
        fail(
            f"[{C.DANGER}]0[/]/{total} tokens valid "
            f"- falling back to unauth endpoint."
        )

    if remove_bad and 0 < working < total:
        good = [t for t in tokens if t not in tm.dead_tokens]
        ensure_file(DEFAULT_TOKEN_FILE)
        Path(DEFAULT_TOKEN_FILE).write_text(
            "\n".join(good), encoding="utf-8"
        )
        info(
            f"Saved {len(good)} valid token(s) back to "
            f"{_TOKEN_FILE_DISPLAY}"
        )

    final_tokens = [t for t in tokens if t not in tm.dead_tokens]
    return final_tokens, remove_bad, working


# ============================================================================
# STEP 3: USERNAMES (with quality filter)
# ============================================================================

def _step_usernames(settings: AppSettings) -> list[str]:
    if settings.length is not None:
        length = settings.length
        count = settings.count if settings.count is not None else 50000
        usernames = _generate_usernames(length, count)
        ok(
            f"Generated [{C.BRAND}]{format_count(len(usernames))}[/] "
            f"usernames (len={length}) from CLI"
        )
        # Apply quality filter even in CLI mode
        usernames = _apply_quality_filter(usernames)
        ensure_file(DEFAULT_NAMES_FILE)
        Path(DEFAULT_NAMES_FILE).write_text(
            "\n".join(usernames), encoding="utf-8"
        )
        return usernames

    raw = Prompt.ask(
        f"[{C.BRAND}](f)ile[/] or [{C.BRAND}](g)enerate[/] usernames?",
        choices=["f", "g"],
        default="f",
    )
    mode = "generate" if raw == "g" else "file"
    usernames: list[str] = []

    if mode == "file":
        names_path = Prompt.ask(
            f"[{C.BRAND}]Path to username file[/]",
            default=_NAMES_FILE_DISPLAY,
        )
        usernames = load_lines(_resolve_input_path(names_path))
        if not usernames:
            warn("File is empty - switching to generate mode.")
            mode = "generate"
        else:
            ok(
                f"Loaded [{C.BRAND}]{format_count(len(usernames))}[/] "
                f"usernames"
            )

    if mode == "generate":
        length = IntPrompt.ask(
            f"[{C.BRAND}]Username length[/]",
            default=4,
            choices=["2", "3", "4", "5"],
        )
        count = IntPrompt.ask(
            f"[{C.BRAND}]How many?[/]",
            default=50000,
        )
        usernames = _generate_usernames(length, count)
        ok(
            f"Generated [{C.BRAND}]{format_count(len(usernames))}[/] "
            f"usernames"
        )

    # Apply quality filter
    usernames = _apply_quality_filter(usernames)

    ensure_file(DEFAULT_NAMES_FILE)
    Path(DEFAULT_NAMES_FILE).write_text(
        "\n".join(usernames), encoding="utf-8"
    )
    return usernames


def _apply_quality_filter(usernames: list[str]) -> list[str]:
    """Apply the quality filter and print a summary card."""
    if not usernames:
        return usernames

    try:
        from quality import filter_by_quality, score_to_tier, score_username
    except ImportError:
        return usernames

    original = len(usernames)
    kept, dropped = filter_by_quality(
        usernames, min_score=QUALITY_MIN_SCORE
    )

    if not dropped:
        return usernames

    # Tier histogram
    tiers = {"premium": 0, "excellent": 0, "good": 0, "fair": 0}
    for n in kept:
        t = score_to_tier(score_username(n))
        if t in tiers:
            tiers[t] += 1

    console.print()
    console.print(quality_card({
        "kept": len(kept),
        "dropped": len(dropped),
        "premium": tiers["premium"],
        "excellent": tiers["excellent"],
        "good": tiers["good"],
        "fair": tiers["fair"],
    }))

    info(
        f"Kept [bold {C.SUCCESS}]{format_count(len(kept))}[/] of "
        f"[bold]{format_count(original)}[/] names "
        f"([dim]dropped {format_count(len(dropped))} low-quality[/])"
    )
    return kept


def _generate_usernames(length: int, count: int) -> list[str]:
    cap = len(USERNAME_CHARS) ** length
    if count > cap:
        count = cap

    if cap <= 500_000:
        pool: list[str] = []

        def rec(prefix: str) -> None:
            if len(pool) >= count:
                return
            if len(prefix) == length:
                if is_valid_username(prefix):
                    pool.append(prefix)
                return
            for c in USERNAME_CHARS:
                rec(prefix + c)
                if len(pool) >= count:
                    return

        rec("")
        return pool

    seen: set[str] = set()
    result: list[str] = []
    max_attempts = count * 20
    attempts = 0
    while len(result) < count and attempts < max_attempts:
        attempts += 1
        cand = "".join(random.choices(USERNAME_CHARS, k=length))
        if cand in seen:
            continue
        if not is_valid_username(cand):
            continue
        seen.add(cand)
        result.append(cand)
    return result


# ============================================================================
# STEP 4: SPEED (with Direct Mode auto-detect)
# ============================================================================

def _step_speed(
    proxies: list[str],
    scraped: bool = False,
    has_token: bool = False,
) -> tuple[int, int, bool]:
    """Guide user through concurrency and timeout.

    Returns (concurrency, timeout, direct_mode).
    Auto-suggests Direct Mode when the proxy pool is too small and a
    valid token is available.
    """

    # ── Direct Mode auto-detect ──
    if has_token and len(proxies) < DIRECT_MODE_MIN_PROXIES:
        console.print()
        info_card(
            f"[{C.WARNING}]{Glyph.WARN} Only "
            f"[bold]{len(proxies)}[/] proxies — too few to be useful[/]",
            f"[{C.SUCCESS}]{Glyph.OK} Suggestion: run "
            f"[bold]DIRECT[/] with your token[/]",
            f"[{C.MUTED}]· Discord AUTH endpoint: ~2.5 req/s stable[/]",
            f"[{C.MUTED}]· Expected: 95%+ success (vs <5% with free proxies)[/]",
        )
        if Confirm.ask(
            f"[{C.BRAND}]{Glyph.ROCKET} Use DIRECT mode (no proxies)?[/]",
            default=True,
        ):
            info(
                f"Direct mode: {DIRECT_MODE_WORKERS} workers, "
                f"no proxies, AUTH endpoint."
            )
            return DIRECT_MODE_WORKERS, DIRECT_MODE_TIMEOUT, True

    # ── Proxyless (no token, no proxies) ──
    if not proxies:
        info("Proxyless mode — 1 worker with delay.")
        delay = IntPrompt.ask(
            f"[{C.BRAND}]Delay between requests (seconds)[/]",
            default=5,
        )
        return 1, delay, False

    # ── Scraped free proxies ──
    if scraped:
        info("Free proxy mode — high concurrency, short timeout.")
        conc = IntPrompt.ask(
            f"[{C.BRAND}]Concurrent workers[/]", default=50
        )
        if conc > MAX_CONCURRENCY:
            warn_card(f"Capped at {MAX_CONCURRENCY}.")
            conc = MAX_CONCURRENCY
        timeout = IntPrompt.ask(
            f"[{C.BRAND}]Request timeout (seconds)[/]",
            default=5,
        )
        return conc, timeout, False

    # ── Static user-supplied proxies ──
    default_conc = min(MAX_CONCURRENCY, max(10, len(proxies) * 5))
    concurrency = IntPrompt.ask(
        f"[{C.BRAND}]Concurrent workers[/]",
        default=default_conc,
    )
    if concurrency > MAX_CONCURRENCY:
        warn_card(f"Capped at {MAX_CONCURRENCY}.")
        concurrency = MAX_CONCURRENCY
    timeout = IntPrompt.ask(
        f"[{C.BRAND}]Request timeout (seconds)[/]",
        default=10,
    )
    return concurrency, timeout, False


# ============================================================================
# STEP 5a: WEBHOOK
# ============================================================================

def _step_webhook(config: Config) -> tuple[str | None, str | None, int]:
    saved_url = config.get("webhook")
    saved_msg = config.get(
        "webhook_message", "**<name>** available | <t:time:R>"
    )
    saved_min = int(config.get("webhook_min_hits", 1))
    always = config.get("webhook_always", False)

    if always and saved_url:
        ok("Using saved webhook")
        return saved_url, saved_msg, saved_min

    if saved_url and not always:
        if not Confirm.ask(
            f"[{C.BRAND}]Use webhook?[/] "
            f"[dim](saved from last session)[/]",
            default=True,
        ):
            if Confirm.ask(
                f"[{C.MUTED}]Forget saved webhook?[/]", default=False
            ):
                config.set("webhook", "")
                config.set("webhook_message", "")
                config.set("webhook_min_hits", 1)
            return None, None, 1
        webhook_url = saved_url
        webhook_msg = saved_msg
    else:
        if not Confirm.ask(
            f"[{C.BRAND}]Send hits to a Discord webhook?[/]",
            default=False,
        ):
            return None, None, 1
        webhook_url = Prompt.ask(f"[{C.BRAND}]Webhook URL[/]")
        if not webhook_url.strip():
            info("Empty URL - webhook disabled.")
            return None, None, 1
        console.print(
            f"[{C.MUTED}]Hits are sent in batches to avoid "
            f"rate-limits.[/]"
        )
        webhook_msg = Prompt.ask(
            f"[{C.BRAND}]Message template per hit[/] "
            f"[dim](<name> <time> <elapsed>)[/]",
            default="**<name>** available | <t:time:R>",
        )

    min_hits = IntPrompt.ask(
        f"[{C.BRAND}]Send only after N hits are collected?[/] "
        f"[dim](1 = immediate)[/]",
        default=saved_min,
    )
    if min_hits < 1:
        min_hits = 1

    config.set("webhook", webhook_url)
    config.set("webhook_message", webhook_msg)
    config.set("webhook_min_hits", min_hits)

    if not always:
        if Confirm.ask(
            f"[{C.MUTED}]Always use this webhook? "
            f"(skip asking next time)[/]",
            default=True,
        ):
            config.set("webhook_always", True)

    return webhook_url, webhook_msg, min_hits


# ============================================================================
# STEP 5b: RESUME OPTIONS
# ============================================================================

def _step_resume_options(
    config: Config, settings: AppSettings
) -> bool:
    from config import HITS_FILE

    hits_exist = HITS_FILE.exists() and HITS_FILE.stat().st_size > 0

    if settings.resume_from_hits:
        return True

    if not hits_exist:
        return False

    console.print()
    info_card(
        f"[{C.BRAND}]{Glyph.INFO}[/] hits.txt has previous results "
        f"- can be used to skip re-checking."
    )
    return Confirm.ask(
        f"[{C.BRAND}]Skip names already in hits.txt?[/]",
        default=True,
    )


# ============================================================================
# PROXY SCRAPER
# ============================================================================

async def _scrape_proxies() -> list[str]:
    console.print()
    console.print(
        f"[{C.BRAND}]{Glyph.SPARK}[/] Scraping "
        f"[bold]{len(PROXY_SOURCES)}[/] sources "
        f"[{C.MUTED}](this may take 15-30s)[/]"
    )
    console.print()

    stats = {
        "sources_ok": 0,
        "sources_fail": 0,
        "raw_total": 0,
    }

    async def _fetch_one(src: ProxySource) -> list[str]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12),
                trust_env=False,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; "
                        "VexUserCheck/3.3)"
                    )
                },
            ) as sess:
                async with sess.get(src.url) as resp:
                    if resp.status != 200:
                        stats["sources_fail"] += 1
                        console.print(
                            f"  [{C.DANGER}]{Glyph.FAIL}[/] "
                            f"{src.name:<22} "
                            f"[{C.MUTED}]HTTP {resp.status}[/]"
                        )
                        return []

                    text = await resp.text()
                    found: list[str] = []
                    for line in text.splitlines():
                        cleaned = _clean_proxy_line(line)
                        if cleaned:
                            found.append(cleaned)

                    if found:
                        stats["sources_ok"] += 1
                        stats["raw_total"] += len(found)
                        console.print(
                            f"  [{C.SUCCESS}]{Glyph.OK}[/] "
                            f"{src.name:<22} "
                            f"[{C.BRAND}]{format_count(len(found))}[/]"
                        )
                    else:
                        stats["sources_fail"] += 1
                        console.print(
                            f"  [{C.WARNING}]{Glyph.WARN}[/] "
                            f"{src.name:<22} "
                            f"[{C.MUTED}]empty[/]"
                        )
                    return found
        except asyncio.TimeoutError:
            stats["sources_fail"] += 1
            console.print(
                f"  [{C.DANGER}]{Glyph.FAIL}[/] {src.name:<22} "
                f"[{C.MUTED}]timeout[/]"
            )
            return []
        except Exception as e:
            stats["sources_fail"] += 1
            console.print(
                f"  [{C.DANGER}]{Glyph.FAIL}[/] {src.name:<22} "
                f"[{C.MUTED}]{type(e).__name__}[/]"
            )
            return []

    tasks = [_fetch_one(src) for src in PROXY_SOURCES]
    results = await asyncio.gather(*tasks)

    # Dedup by host:port
    seen: set[str] = set()
    all_proxies: list[str] = []
    for batch in results:
        for p in batch:
            key = p.split("@")[-1] if "@" in p else p
            if key not in seen:
                seen.add(key)
                all_proxies.append(p)

    # Hard cap
    if len(all_proxies) > MAX_PROXIES_CAP:
        console.print(
            f"  [{C.WARNING}]{Glyph.WARN}[/] "
            f"Capping at [{C.BRAND}]{format_count(MAX_PROXIES_CAP)}[/] "
            f"[{C.MUTED}](from "
            f"{format_count(len(all_proxies))})[/]"
        )
        all_proxies = random.sample(all_proxies, MAX_PROXIES_CAP)

    console.print()
    console.print(f"  [{C.MUTED}]{Glyph.DASH * 40}[/]")
    console.print(
        f"  [{C.BRAND}]Sources:[/] "
        f"[{C.SUCCESS}]{stats['sources_ok']} ok[/] . "
        f"[{C.DANGER}]{stats['sources_fail']} failed[/] . "
        f"[{C.BRAND}]{format_count(stats['raw_total'])} raw[/] . "
        f"[{C.SUCCESS}]{format_count(len(all_proxies))} unique[/]"
    )

    if not all_proxies:
        fail("All sources failed - no proxies.")
        return []

    ok(
        f"[{C.SUCCESS}]{format_count(len(all_proxies))}[/] unique "
        f"proxies [{C.MUTED}](~2-5% usually work)[/]"
    )
    return all_proxies
