#!/usr/bin/env python3
"""VexUserCheck v3.3 - Terminal UI, helpers (ETA, sound, validation cards).

A dark-terminal optimised interface built on top of Rich. Provides:
- welcome()      -- full-screen startup banner
- banner()       -- compact panel used inside the run
- live_card()    -- real-time checker display (ETA, RPS, buckets)
- quality_card() -- name-quality filter summary
- bucket_card()  -- rate-limit bucket snapshot
- validation cards for tokens & proxies
- final_summary() -- end-of-run report
- cross-platform sound + desktop notification
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import (
    C,
    Glyph,
    VERSION,
    APP_NAME,
    APP_TAGLINE,
    BANNER_ASCII,
    BANNER_SUBTITLE,
)


console = Console()


# ---------------------------------------------------------------------------
# Sound & desktop notification
# ---------------------------------------------------------------------------

_sound_lock = threading.Lock()
_last_sound_time = 0.0

_MACOS_SOUND = "/System/Library/Sounds/Glass.aiff"
_LINUX_SOUNDS = [
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/gnome/default/alerts/glass.ogg",
    "/usr/share/sounds/ubuntu/stereo/message.ogg",
]


def _play_macos() -> bool:
    if not os.path.exists(_MACOS_SOUND):
        return False
    if not shutil.which("afplay"):
        return False
    try:
        subprocess.Popen(
            ["afplay", _MACOS_SOUND],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _play_linux() -> bool:
    paplay = shutil.which("paplay") or shutil.which("aplay")
    if not paplay:
        return False
    for snd in _LINUX_SOUNDS:
        if os.path.exists(snd):
            try:
                subprocess.Popen(
                    [paplay, snd],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                continue
    return False


def _play_bell() -> bool:
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
        return True
    except Exception:
        return False


def play_hit_sound(min_interval: float = 2.0) -> None:
    """Cross-platform notification on hit. Rate-limited, never raises."""
    global _last_sound_time
    with _sound_lock:
        now = time.time()
        if now - _last_sound_time < min_interval:
            return
        _last_sound_time = now

    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "Username found!" with title "{APP_NAME}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    played = False
    if sys.platform == "darwin":
        played = _play_macos()
    elif sys.platform.startswith("linux"):
        played = _play_linux()

    if not played:
        _play_bell()


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def format_eta(seconds: float) -> str:
    """Format a duration as `1h 5m` / `3m 42s` / `12s`. Never overflows."""
    if seconds <= 0 or seconds > 86400:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_count(n: int) -> str:
    """Add thousands separators: 1234567 → 1,234,567"""
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Welcome screen
# ---------------------------------------------------------------------------

def welcome() -> None:
    """Full-screen startup banner. Call once per session."""
    console.clear()
    console.print()

    art = Text(BANNER_ASCII, style=f"bold {C.BRAND}")
    console.print(art, justify="center")

    subtitle = Text(BANNER_SUBTITLE, style=f"bold {C.BRAND_ALT}")
    console.print(subtitle, justify="center")
    console.print()

    tagline = Text(APP_TAGLINE, style=f"italic {C.MUTED}")
    console.print(tagline, justify="center")
    console.print()

    info = Table.grid(padding=(0, 2))
    info.add_column(style=C.DIM, justify="right")
    info.add_column(style=C.TEXT, justify="left")
    info.add_row("Version", f"[{C.BRAND}]{VERSION}[/]")
    info.add_row("Mode",    f"[{C.SUCCESS}]async · concurrent[/]")
    info.add_row("Engine",  f"[{C.INFO}]aiohttp · asyncio[/]")

    console.print(Panel(
        info,
        title=f"[bold {C.BRAND}]{APP_NAME}[/]",
        title_align="center",
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 6),
    ))
    console.print()


def banner() -> Panel:
    """Compact banner used inside the run (e.g. `-n` mode)."""
    inner = Text()
    inner.append(APP_NAME, style=f"bold {C.BRAND}")
    inner.append(f"  v{VERSION}", style=f"{C.MUTED}")
    return Panel(
        inner,
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 4),
    )


# ---------------------------------------------------------------------------
# Progress indicator (5-step wizard)
# ---------------------------------------------------------------------------

def progress_steps(current: int, total: int = 5) -> str:
    """Render the wizard step indicator with glyphs."""
    steps = ["Proxies", "Tokens", "Usernames", "Speed", "Webhook"]
    parts: list[str] = []
    for i, label in enumerate(steps):
        if i < current:
            parts.append(f"[{C.SUCCESS}]{Glyph.OK}[/] {label}")
        elif i == current:
            parts.append(f"[{C.BRAND}]{Glyph.BULLET}[/] [bold]{label}[/]")
        else:
            parts.append(f"[{C.DIM}]{Glyph.CIRCLE}[/] {label}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Section header & divider
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    """Print a clean section header."""
    console.print()
    console.print(Text(title, style=f"bold {C.BRAND}"))
    console.print(Glyph.DASH * 40, style=C.BORDER)


def hr() -> None:
    """Full-width thin divider."""
    console.print(Glyph.HBAR * 60, style=C.BORDER)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def card(title: str | None, *lines: str, border: str = C.BRAND) -> None:
    """Print a rounded-card panel."""
    body = "\n".join(lines) if lines else ""
    console.print(Panel(
        body,
        title=title,
        title_align="left",
        box=box.ROUNDED,
        border_style=border,
        padding=(1, 2),
    ))


def info_card(*lines: str) -> None:
    card(None, *lines, border=C.BORDER)


def warn_card(*lines: str) -> None:
    card(None, *lines, border=C.WARNING)


def success_card(*lines: str) -> None:
    card(None, *lines, border=C.SUCCESS)


# ---------------------------------------------------------------------------
# Configuration summary
# ---------------------------------------------------------------------------

def config_summary(
    proxy_count: int,
    scraped: bool,
    remove_bad: bool,
    username_count: int,
    concurrency: int,
    timeout: int,
    webhook: bool,
    token_count: int = 0,
    token_working: int = 0,
    resume_from_hits: bool = False,
    resume_progress: bool = True,
    direct_mode: bool = False,
) -> None:
    """Print a compact summary of the run configuration."""
    console.print()
    t = Table(
        box=box.ROUNDED,
        border_style=C.BORDER,
        show_header=False,
        padding=(0, 2),
    )
    t.add_column(style=C.MUTED, width=18)
    t.add_column(style=C.TEXT)

    # Mode line
    if direct_mode:
        t.add_row("Mode", f"[{C.SUCCESS}]DIRECT[/] [dim](no proxies)[/]")
        t.add_row("Proxies", f"[{C.MUTED}]disabled[/]")
    else:
        tag = f" [{C.MUTED}](free)[/]" if scraped else ""
        t.add_row("Proxies", f"[{C.BRAND}]{format_count(proxy_count)}[/] loaded{tag}")
        if proxy_count > 1 and not scraped:
            t.add_row("Auto-remove", "Yes" if remove_bad else "No")

    if token_count > 0:
        if token_working == 0:
            t.add_row(
                "Tokens",
                f"[{C.DANGER}]0[/]/{token_count} [dim](unauth fallback)[/]",
            )
        else:
            t.add_row(
                "Tokens",
                f"[{C.SUCCESS}]{token_working}[/]/{token_count} valid",
            )
    else:
        t.add_row("Tokens", f"[{C.MUTED}]none (unauth mode)[/]")

    t.add_row("Usernames", format_count(username_count))
    t.add_row("Workers",   str(concurrency))
    t.add_row("Timeout",   f"{timeout}s")
    t.add_row("Webhook",   f"[{C.SUCCESS}]on[/]" if webhook else f"[{C.MUTED}]off[/]")
    t.add_row(
        "Resume hits",
        f"[{C.SUCCESS}]on[/]" if resume_from_hits else f"[{C.MUTED}]off[/]",
    )
    t.add_row(
        "Resume prog.",
        f"[{C.SUCCESS}]on[/]" if resume_progress else f"[{C.MUTED}]off[/]",
    )

    console.print(Panel(
        t,
        title=f"[bold {C.BRAND}]Summary[/]",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BRAND,
    ))


# ---------------------------------------------------------------------------
# Token validation card
# ---------------------------------------------------------------------------

def token_validation_card(done: int, total: int, working: int, dead: int) -> Panel:
    pct = done / max(total, 1) * 100
    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)

    inner.add_row(
        "Valid",   f"[{C.SUCCESS}]{working}[/]",
        "Invalid", f"[{C.DANGER}]{dead}[/]",
    )
    inner.add_row(
        "Progress", f"[{C.BRAND}]{done}[/]/{total} ({pct:.0f}%)",
        "Total",    str(total),
    )

    return Panel(
        inner,
        title=f"[bold {C.BRAND}]{Glyph.SHIELD} Validating tokens[/] · {done}/{total}",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Proxy validation card
# ---------------------------------------------------------------------------

def proxy_validation_card(done: int, total: int, working: int, dead: int) -> Panel:
    pct = done / max(total, 1) * 100
    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)

    inner.add_row(
        "Working", f"[{C.SUCCESS}]{working}[/]",
        "Dead",    f"[{C.DANGER}]{dead}[/]",
    )
    inner.add_row(
        "Progress", f"[{C.BRAND}]{done}[/]/{total} ({pct:.0f}%)",
        "Total",    str(total),
    )

    return Panel(
        inner,
        title=f"[bold {C.BRAND}]{Glyph.BOLT} Validating proxies[/] · {done}/{total}",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Quality filter card (NEW)
# ---------------------------------------------------------------------------

def quality_card(stats: dict) -> Panel:
    """Display name-quality filtering statistics.

    Expected keys: kept, dropped, premium, excellent, good, fair.
    """
    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=16, no_wrap=True)
    inner.add_column(style=C.TEXT)
    inner.add_column(style=C.MUTED, width=16, no_wrap=True)
    inner.add_column(style=C.TEXT)

    inner.add_row(
        "Kept",
        f"[bold {C.SUCCESS}]{format_count(stats.get('kept', 0))}[/]",
        "Dropped",
        f"[{C.DANGER}]{format_count(stats.get('dropped', 0))}[/]",
    )
    inner.add_row(
        f"[{C.GOLD}]Premium (≥85)[/]",
        f"[{C.GOLD}]{format_count(stats.get('premium', 0))}[/]",
        "Excellent (≥65)",
        f"[{C.SUCCESS}]{format_count(stats.get('excellent', 0))}[/]",
    )
    inner.add_row(
        "Good (≥45)",
        f"[{C.BRAND}]{format_count(stats.get('good', 0))}[/]",
        "Fair (≥25)",
        f"[{C.MUTED}]{format_count(stats.get('fair', 0))}[/]",
    )

    return Panel(
        inner,
        title=f"[bold {C.BRAND}]{Glyph.STAR} Name Quality Filter[/]",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BRAND,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Bucket rate-limit card (NEW)
# ---------------------------------------------------------------------------

def bucket_card(snapshot: dict) -> Panel:
    """Display live rate-limit bucket statistics.

    Expected keys: buckets, active, total_429, total_waits,
                   soonest_reset, global_wait.
    """
    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=16, no_wrap=True)
    inner.add_column(style=C.TEXT)
    inner.add_column(style=C.MUTED, width=16, no_wrap=True)
    inner.add_column(style=C.TEXT)

    active = snapshot.get("active", 0)
    total = snapshot.get("buckets", 0)
    active_color = C.DANGER if active > 0 else C.SUCCESS

    inner.add_row(
        "Buckets",
        f"[{C.BRAND}]{format_count(total)}[/]",
        "Active (capped)",
        f"[{active_color}]{format_count(active)}[/]",
    )
    inner.add_row(
        "Total 429s",
        f"[{C.DANGER}]{format_count(snapshot.get('total_429', 0))}[/]",
        "Waits",
        f"[{C.WARNING}]{format_count(snapshot.get('total_waits', 0))}[/]",
    )
    global_wait = snapshot.get("global_wait", 0.0)
    soonest = snapshot.get("soonest_reset", 0.0)
    inner.add_row(
        "Soonest reset",
        f"[{C.BRAND}]{format_eta(soonest)}[/]",
        "Global lock",
        f"[{C.WARNING}]{format_eta(global_wait)}[/]" if global_wait > 0
        else f"[{C.SUCCESS}]none[/]",
    )

    return Panel(
        inner,
        title=f"[bold {C.BRAND}]{Glyph.SHIELD} Rate-Limit Buckets[/]",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BORDER,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Live checker card
# ---------------------------------------------------------------------------

def live_card(
    done: int,
    total: int,
    works: int,
    taken: int,
    requests: int,
    ratelimited: int,
    circuit_opens: int,
    rps: float,
    elapsed: float,
    proxy_alive: int,
    checks_rps: float = 0.0,
    paused: bool = False,
    recent: list[str] | None = None,
    feed: list[str] | None = None,
    tokens_alive: int = 0,
    tokens_total: int = 0,
    eta_seconds: float = 0.0,
    bucket_active: int = 0,
    bucket_total: int = 0,
) -> Panel:
    """Build the live display panel for the checker runner.

    proxy_alive == -1 signals "direct mode" (no proxies in use).
    """

    pct = done / max(total, 1) * 100
    direct_mode = proxy_alive == -1

    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style=C.TEXT)

    inner.add_row(
        "Available", f"[bold {C.SUCCESS}]{format_count(works)}[/]",
        "Taken",     f"[bold {C.DANGER}]{format_count(taken)}[/]",
    )
    inner.add_row(
        "Req/s",    f"[{C.BRAND}]{rps:.0f}[/]",
        "Requests", format_count(requests),
    )

    if ratelimited > 0:
        inner.add_row(
            f"[{C.WARNING}]{Glyph.CLOCK} Rate limited[/]",
            f"[{C.WARNING}]{format_count(ratelimited)}[/]",
            "Elapsed", f"{elapsed:.0f}s",
        )
    else:
        inner.add_row(
            "Progress", f"{format_count(done)}/{format_count(total)} ({pct:.1f}%)",
            "Elapsed",  f"{elapsed:.0f}s",
        )

    inner.add_row(
        f"{Glyph.ROCKET} ETA", f"[{C.BRAND}]{format_eta(eta_seconds)}[/]",
        "Checks/s", f"[{C.BRAND}]{checks_rps:.1f}[/]",
    )

    # Mode / proxies row
    if direct_mode:
        inner.add_row(
            "Mode", f"[{C.SUCCESS}]direct (no proxy)[/]",
            "Workers", "active",
        )
    elif circuit_opens > 0 or paused:
        inner.add_row(
            f"[{C.WARNING}]{Glyph.PAUSE} Circuit breaks[/]",
            f"[{C.WARNING}]{circuit_opens}[/]",
            "Proxies", f"{proxy_alive} alive",
        )
    else:
        inner.add_row(
            "Proxies", f"{proxy_alive} alive",
            "Workers", "active",
        )

    if tokens_total > 0:
        color = C.SUCCESS if tokens_alive > 0 else C.DANGER
        inner.add_row(
            "Tokens",
            f"[{color}]{tokens_alive}[/]/{tokens_total} alive",
            "", "",
        )

    if bucket_total > 0:
        color = C.DANGER if bucket_active > 0 else C.SUCCESS
        inner.add_row(
            f"{Glyph.SHIELD} Buckets",
            f"[{color}]{bucket_active}[/]/{bucket_total} active",
            "", "",
        )

    content = Table(box=None, show_header=False, padding=(0, 0), expand=True)
    content.add_row(inner)

    # Rate-limit alert
    if ratelimited > 0 and (done == 0 or ratelimited > done * 2):
        content.add_section()
        content.add_row(Text(
            f"{Glyph.WARN} Discord is rate-limiting — "
            f"{format_count(ratelimited)} requests blocked.",
            style=C.WARNING,
        ))

    if paused:
        content.add_section()
        content.add_row(Text(
            f"{Glyph.PAUSE} Circuit breaker active — workers paused briefly.",
            style=C.WARNING,
        ))

    # Recent hits
    if recent:
        content.add_section()
        recent_str = "  ".join(f"[{C.SUCCESS}]{n}[/]" for n in recent[-6:])
        content.add_row(Text("Recent  ", style=C.MUTED) + Text.from_markup(recent_str))

    # Live feed
    if feed:
        content.add_section()
        feed_str = "  ".join(feed[-8:])
        content.add_row(Text.from_markup(feed_str))

    if ratelimited > 0 and done == 0:
        status = f"{Glyph.CLOCK} rate-limited"
    else:
        status = f"{format_count(done)}/{format_count(total)} ({pct:.1f}%)"

    title_extra = " [dim]· direct[/]" if direct_mode else ""

    return Panel(
        content,
        title=(
            f"[bold {C.BRAND}]{APP_NAME}[/]{title_extra} · {status} "
            f"· [dim]{elapsed:.0f}s[/]"
        ),
        title_align="left",
        box=box.ROUNDED,
        border_style=C.BORDER if not paused else C.WARNING,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

def final_summary(
    requests: int,
    works: int,
    taken: int,
    ratelimited: int,
    circuit_opens: int,
    elapsed: float,
    peak_rps: float = 0.0,
    best_streak: int = 0,
    token_401: int = 0,
    tokens_alive: int = 0,
    tokens_total: int = 0,
) -> None:
    """Print the final results card."""
    console.print()
    avg_rps = requests / max(elapsed, 0.1)

    t = Table(
        box=box.ROUNDED,
        border_style=C.BORDER,
        show_header=False,
        padding=(0, 2),
    )
    t.add_column(style=C.MUTED, width=18)
    t.add_column(style=C.TEXT)

    t.add_row("Available", f"[bold {C.SUCCESS}]{format_count(works)}[/]")
    t.add_row("Taken",     f"[bold {C.DANGER}]{format_count(taken)}[/]")
    t.add_row("Requests",  format_count(requests))

    if ratelimited > 0:
        t.add_row("Rate limited", f"[{C.WARNING}]{format_count(ratelimited)}[/]")
    if circuit_opens > 0:
        t.add_row("Circuit breaks", str(circuit_opens))
    if token_401 > 0:
        t.add_row("Token 401s", f"[{C.DANGER}]{token_401}[/]")
    if tokens_total > 0:
        color = C.SUCCESS if tokens_alive > 0 else C.DANGER
        t.add_row("Tokens alive", f"[{color}]{tokens_alive}[/]/{tokens_total}")

    t.add_row("Elapsed",    f"{elapsed:.0f}s")
    t.add_row("Avg req/s",  f"{avg_rps:.1f}")

    if peak_rps > 0:
        t.add_row("Peak req/s", f"[{C.BRAND}]{peak_rps:.0f}[/]")
    if best_streak > 1:
        t.add_row("Best streak", f"[{C.GOLD}]{best_streak}[/] hits")
    if works > 0:
        t.add_row("Saved to", "results/hits.txt")

    console.print(Panel(
        t,
        title=f"[bold {C.SUCCESS}]{Glyph.OK} Done[/] · {elapsed:.0f}s",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.SUCCESS,
    ))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def ok(msg: str) -> None:
    console.print(f"  [{C.SUCCESS}]{Glyph.OK}[/] {msg}")


def fail(msg: str) -> None:
    console.print(f"  [{C.DANGER}]{Glyph.FAIL}[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"  [{C.WARNING}]{Glyph.WARN}[/] {msg}")


def info(msg: str) -> None:
    console.print(f"  [{C.MUTED}]{Glyph.INFO}[/] {msg}")


def print_step(n: int, total: int, title: str) -> None:
    """Print a numbered step header during the wizard."""
    console.print()
    console.print(
        f"[bold {C.BRAND}]Step {n}/{total}[/] [dim]·[/] [bold]{title}[/]"
    )
    console.print(Glyph.DASH * 40, style=C.BORDER)
