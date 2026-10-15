#!/usr/bin/env python3
"""VexUserCheck v3.3 - Configuration, constants, helpers, data models."""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
RESULTS_DIR = PROJECT_ROOT / "results"

HITS_FILE = RESULTS_DIR / "hits.txt"
PROGRESS_FILE = DATA_DIR / "progress.json"
DEAD_TOKENS_FILE = DATA_DIR / "dead_tokens.txt"
PROXY_CACHE_FILE = DATA_DIR / "proxy_cache.txt"

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

VERSION = "3.3.0"
APP_NAME = "VexUserCheck"
APP_TAGLINE = "Async Discord Username Availability Checker"
APP_AUTHOR = "Vex"
APP_REPO = "https://github.com/vex/VexUserCheck"

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

ENDPOINT = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
AUTH_ENDPOINT = "https://discord.com/api/v9/unique-username/username-attempt"
ME_ENDPOINT = "https://discord.com/api/v9/users/@me"

# ---------------------------------------------------------------------------
# Runtime limits
# ---------------------------------------------------------------------------

USERNAME_CHARS = string.ascii_lowercase + string.digits + "_" + "."
MAX_CONCURRENCY = 2000

# Proxy pipeline
MAX_PROXIES_CAP = 20_000
PROXY_VALIDATE_THRESHOLD = 500
PROXY_VALIDATE_TIMEOUT = 4.0
PROXY_VALIDATE_CONCURRENCY = 100
PROXY_WARMUP_TIMEOUT = 3.0
PROXY_WARMUP_CONCURRENCY = 200
PROXY_WARMUP_MIN_ALIVE = 100

# Proxy scoring
PROXY_START_SCORE = 3
PROXY_SCORE_HIT = 5
PROXY_SCORE_MISS = 1
PROXY_MAX_SCORE = 100

# ─── Direct mode (NEW in v3.3) ───
DIRECT_MODE_MIN_PROXIES = 5
DIRECT_MODE_RATE_LIMIT = 2.5
DIRECT_MODE_JITTER = 0.15
DIRECT_MODE_WORKERS = 2
DIRECT_MODE_TIMEOUT = 10

# ─── Quality scoring ───
QUALITY_MIN_SCORE = 25
QUALITY_PREMIUM_SCORE = 85

# ─── Bucket rate limit ───
BUCKET_SAFETY_MARGIN = 1
BUCKET_GLOBAL_COOLDOWN = 1.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-\.]{40,}$")


# ---------------------------------------------------------------------------
# ASCII art
# ---------------------------------------------------------------------------

BANNER_ASCII = (
    " ██╗   ██╗███████╗██╗  ██╗\n"
    " ██║   ██║██╔════╝╚██╗██╔╝\n"
    " ██║   ██║█████╗   ╚███╔╝\n"
    " ╚██╗ ██╔╝██╔══╝   ██╔██╗\n"
    "  ╚████╔╝ ███████╗██╔╝ ██╗\n"
    "   ╚═══╝  ╚══════╝╚═╝  ╚═╝"
)

BANNER_SUBTITLE = "       U S E R   C H E C K"


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

class C:
    BRAND = "#0A84FF"
    BRAND_ALT = "#5AC8FA"
    BRAND_DEEP = "#0040DD"
    SUCCESS = "#30D158"
    DANGER = "#FF453A"
    WARNING = "#FF9F0A"
    INFO = "#64D2FF"
    TEXT = "#FFFFFF"
    MUTED = "#98989D"
    DIM = "#636366"
    BORDER = "#48484A"
    HIGHLIGHT = "#BF5AF2"
    GOLD = "#FFD60A"


class Glyph:
    OK = "✓"
    FAIL = "✗"
    WARN = "⚠"
    INFO = "ℹ"
    ARROW = "→"
    BULLET = "●"
    CIRCLE = "○"
    DOT = "·"
    DASH = "─"
    HBAR = "━"
    SPARK = "◆"
    STAR = "★"
    CLOCK = "⏳"
    PAUSE = "⏸"
    ROCKET = "🚀"
    TARGET = "🎯"
    SHIELD = "🛡"
    BOLT = "⚡"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dir(*paths: str | Path) -> None:
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def ensure_file(filepath: str | Path, *, clean: bool = False) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    if clean or not path.exists():
        path.write_text("", encoding="utf-8")


def load_lines(filepath: str | Path) -> list[str]:
    path = Path(filepath)
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def is_valid_username(name: str) -> bool:
    if not (2 <= len(name) <= 32):
        return False
    if ".." in name:
        return False
    if name.startswith(".") or name.endswith("."):
        return False
    return all(c in USERNAME_CHARS for c in name)


def is_probable_token(token: str) -> bool:
    if not token:
        return False
    token = token.strip()
    if not _TOKEN_RE.match(token):
        return False
    if token.lower() in ("token", "your_token_here", "none", "null"):
        return False
    return True


# ---------------------------------------------------------------------------
# Persistent JSON config
# ---------------------------------------------------------------------------

class Config:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else DATA_DIR / "config.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists() and self._path.stat().st_size > 0:
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        else:
            self._path.write_text("{}", encoding="utf-8")

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_all(self) -> dict:
        return dict(self._data)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AppSettings:
    debug: bool = False
    verbose: bool = False
    no_wizard: bool = False
    resume_from_hits: bool = False
    no_sound: bool = False
    length: int | None = None
    count: int | None = None
    webhook_min_hits: int | None = None
    resume_progress: bool = True


@dataclass
class RunConfig:
    proxies: list[str]
    remove_bad_proxies: bool
    usernames: list[str]
    concurrency: int
    timeout: int
    scraped: bool = False
    webhook_url: str | None = None
    webhook_message: str | None = None
    tokens: list[str] = field(default_factory=list)
    remove_bad_tokens: bool = True
    resume_from_hits: bool = False
    resume_progress: bool = True
    webhook_min_hits: int = 1
    sound_on_hit: bool = True
    direct_mode: bool = False  # NEW


@dataclass
class Stats:
    requests: int = 0
    works: int = 0
    taken: int = 0
    ratelimited: int = 0
    circuit_opens: int = 0
    rps: float = 0.0
    checks_rps: float = 0.0
    peak_rps: float = 0.0
    best_streak: int = 0
    token_401: int = 0
    _streak: int = 0
    _lock: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        import asyncio
        self._lock = asyncio.Lock()

    async def inc_requests(self) -> None:
        async with self._lock:
            self.requests += 1

    async def inc_works(self) -> None:
        async with self._lock:
            self.works += 1
            self._streak += 1
            if self._streak > self.best_streak:
                self.best_streak = self._streak

    async def inc_taken(self) -> None:
        async with self._lock:
            self.taken += 1
            self._streak = 0

    async def inc_ratelimited(self) -> None:
        async with self._lock:
            self.ratelimited += 1

    async def inc_circuit_open(self) -> None:
        async with self._lock:
            self.circuit_opens += 1

    async def inc_token_401(self) -> None:
        async with self._lock:
            self.token_401 += 1

    async def set_rps(self, value: float) -> None:
        async with self._lock:
            self.rps = value
            if value > self.peak_rps:
                self.peak_rps = value

    async def set_checks_rps(self, value: float) -> None:
        async with self._lock:
            self.checks_rps = value

    async def snapshot(self) -> dict:
        async with self._lock:
            return {
                "requests": self.requests,
                "works": self.works,
                "taken": self.taken,
                "ratelimited": self.ratelimited,
                "circuit_opens": self.circuit_opens,
                "rps": self.rps,
                "checks_rps": self.checks_rps,
                "peak_rps": self.peak_rps,
                "best_streak": self.best_streak,
                "token_401": self.token_401,
            }
