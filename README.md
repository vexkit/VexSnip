<img width="384" height="384" alt="file_0000000014188210a597e713570cc764" src="https://github.com/user-attachments/assets/8a66a6d8-9f96-4ccc-bc20-2374e553377c" />



```markdown
<div align="center">

# CloudChecker

**Async Discord username availability checker — fast, resilient, and production-grade.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Async](https://img.shields.io/badge/asyncio-aiohttp-orange)](https://docs.aiohttp.org/)
[![UI](https://img.shields.io/badge/UI-Rich-purple)](https://rich.readthedocs.io/)
[![Version](https://img.shields.io/badge/version-3.1.0-informational)]()

*A single-file-free, multi-module async engine for checking Discord username availability at scale.*

</div>

---

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [The 5-Step Wizard](#the-5-step-wizard)
- [CLI Reference](#cli-reference)
- [Project Layout](#project-layout)
- [Data Files](#data-files)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Ethical Notice](#ethical-notice)
- [License](#license)

---

## Features

| Category | Feature |
|----------|---------|
| **Performance** | Fully async (`asyncio` + `aiohttp`), up to 2000 concurrent workers |
| **Resilience** | Circuit breaker, adaptive backoff, error aggregation |
| **Proxies** | Proxyless / rotating gateway / static list / scored pool |
| **Proxy Scraper** | Built-in scraper from 17+ public sources |
| **Tokens** | Optional token pool with round-robin or scored rotation |
| **Token Validation** | Pre-flight validation via `/users/@me` |
| **Dual Endpoint** | Auto-switches between authed and unauthed endpoints |
| **Resume** | Full progress persistence via `progress.json` |
| **Skip Known** | Skip names already found in `hits.txt` |
| **Live UI** | Rich-based live panel with ETA, RPS, streaks |
| **Webhook** | Adaptive batching, honors `X-RateLimit-*`, `min_hits` support |
| **Notifications** | Cross-platform sound + native desktop notifications |
| **Diagnostics** | Debug mode, structured error aggregation |
| **Python 3.13** | SSL patch included for the latest runtime |

---

## Screenshots

> Terminal UI during a scan:

```

CloudChecker · 45821/200000 (23%) · 342s
Available  412  |  Taken  45390
Req/s      134  |  Requests  98561
Progress   45821/200000 (23%)
ETA        9m 12s  |  Checks/s  134.0
Proxies    4382 alive  |  Workers  active
Tokens     2/2 alive

Recent  vex  neo  echo  flux  nova  oryx
✓ vex  ✗ zed  ✓ neo  ✗ axiom  ✓ echo  ...

```

> Summary on completion:

```

╭─ Done · 1284s ──────────────────────╮
│ Available     512                    │
│ Taken         199452                 │
│ Requests      412039                 │
│ Rate limited  1284                   │
│ Elapsed       1284s                  │
│ Avg req/s     320.9                  │
│ Peak req/s    512                    │
│ Best streak   14 hits                │
│ Saved to      results/hits.txt       │
╰──────────────────────────────────────╯

```

---

## Architecture

```

```

---

## Requirements

- **Python** 3.10 or newer (3.11 / 3.12 recommended)
- **pip** 21+
- **OS**: Linux, macOS, or Windows (via WSL)
- **Optional**: `notify-send` (Linux) or macOS `afplay` for sound

---

## Installation

### Option A — Automatic

```bash
git clone https://github.com/<you>/CloudChecker.git
cd CloudChecker
chmod +x install.sh run.sh
./install.sh
```

The installer:

1. Verifies Python 3
2. Creates a virtual environment at .venv/
3. Installs aiohttp and rich
4. Creates data/, logs/, results/ directories
5. Seeds empty files: proxies.txt, tokens.txt, names_to_check.txt

Option B — Manual

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data logs results
touch data/proxies.txt data/tokens.txt data/names_to_check.txt
```

---

Quick Start

```bash
./run.sh
```

You will be guided through a 5-step wizard. Answer each prompt, then the scan begins.

For repeated usage, skip the wizard with saved settings:

```bash
./run.sh -n --resume-from-hits
```

---

Usage

Basic scan

```bash
./run.sh
```

Skip wizard (use saved config)

```bash
./run.sh -n
```

Generate usernames of a specific length

```bash
./run.sh -n --length 4 --count 200000
```

Resume and skip previously found names

```bash
./run.sh -n --resume-from-hits
```

Webhook with a minimum batch size

```bash
./run.sh -n --webhook-min-hits 10
```

Silent mode (no sound)

```bash
./run.sh -n --no-sound
```

Debug mode (verbose output)

```bash
./run.sh -d
```

---

The 5-Step Wizard

Step Question Choices
1 Proxy source file / paste / scrape / none
2 Token source file / paste / none
3 Usernames file / generate + length + count
4 Speed concurrency + timeout
5 Webhook URL + template + min_hits

Each step is interactive, validates its inputs, and persists the result for future runs.

---

CLI Reference

Flag Description Default
-d, --debug Verbose per-request logging on stderr off
-n, --no-wizard Skip wizard; use saved files off
--resume-from-hits Skip names present in results/hits.txt off
--no-resume-progress Ignore data/progress.json resume enabled
--no-sound Disable notification sound sound on
--length N Username length (requires -n) —
--count N Number of usernames to generate 50000
--webhook-min-hits N Minimum hits before flushing webhook 1
--version Print version and exit —

---

Project Layout

```
CloudChecker/
├── README.md
├── LICENSE
├── requirements.txt
├── install.sh
├── run.sh
│
├── checker.py           # entry point
├── config.py            # constants, Config, Stats
├── proxy.py             # ProxyManager
├── token_manager.py     # TokenManager
├── engine.py            # Checker, CircuitBreaker, WebhookSender
├── progress.py          # ProgressTracker
├── ui.py                # Rich UI, sound, ETA
├── wizard.py            # 5-step wizard + proxy scraper
│
├── data/
│   ├── config.json
│   ├── proxies.txt
│   ├── tokens.txt
│   ├── names_to_check.txt
│   ├── progress.json
│   └── dead_tokens.txt
│
├── logs/
│   └── error.txt
│
└── results/
    └── hits.txt
```

---

Data Files

data/proxies.txt

One proxy per line. Supported formats:

```
host:port
http://host:port
user:pass@host:port
http://user:pass@host:port
socks5://host:port
```

Empty lines and lines starting with # are ignored.

data/tokens.txt

One Discord token per line. Lines starting with # are treated as comments.

data/names_to_check.txt

One username per line. Empty lines and # comments are ignored.

results/hits.txt

Tab-separated columns:

```
name	timestamp	proxy	token_hash
vex	2026-01-12 14:32:11	1.2.3.4:8080	a3f4b2c1
```

· proxy is direct when no proxy was used
· token_hash is unauth when the unauth endpoint was used

data/progress.json

Updated every 5 seconds during a scan. Enables resume:

```json
{
  "idx": 45000,
  "total": 200000,
  "signature": "a1b2c3d4e5f6a7b8",
  "saved_at": 1729000000.0
}
```

---

How It Works

1. Setup — Wizard collects proxies, tokens, usernames, speed, and webhook config.
2. Validation — Tokens are validated in parallel via /users/@me. Proxies can be validated optionally.
3. Dispatch — An atomic index distributes usernames across N async workers.
4. Per-request — Each worker acquires a proxy and a token, POSTs to the appropriate endpoint, and parses the response.
5. Retry logic — 429 triggers cooldowns; 401 marks the token dead; network errors back off progressively.
6. Write — Available names are appended to hits.txt with metadata; the webhook is notified if enabled.
7. Progress — The index is checkpointed every 5 seconds.
8. Completion — A final summary is displayed; progress.json is updated one last time.

---

Configuration

data/config.json is written automatically. You may edit it by hand:

```json
{
  "reuse_proxies": true,
  "reuse_tokens": true,
  "remove_proxies": true,
  "remove_tokens": true,
  "concurrency": 100,
  "timeout": 5,
  "webhook": "",
  "webhook_message": "**<name>** available | <t:time:R>",
  "webhook_min_hits": 1,
  "webhook_always": false
}
```

---

Troubleshooting

<details>
<summary><b>ModuleNotFoundError: aiohttp</b></summary>

The virtual environment is not active. Run:

```bash
./install.sh
```

</details>

<details>
<summary><b>Permission denied: ./run.sh</b></summary>

```bash
chmod +x run.sh install.sh
```

</details>

<details>
<summary><b>All requests return 403</b></summary>

Your proxies are blocked. Either:

· Replace them with fresh ones.
· Run without proxies (n in Step 1).
· Use the built-in scraper (s in Step 1).

</details>

<details>
<summary><b>0/N tokens valid</b></summary>

All tokens are expired or malformed. The checker falls back to the unauthenticated endpoint automatically — no action required.

</details>

<details>
<summary><b>Constant rate-limiting</b></summary>

Lower the concurrency, add more proxies, or add tokens. The authenticated endpoint has higher rate limits.

</details>

<details>
<summary><b>Webhook not firing</b></summary>

Check webhook_min_hits in data/config.json. If you set it to 10 and only received 3 hits, nothing is sent until the run ends (which force-flushes the buffer).

</details>

<details>
<summary><b>Python 3.13 SSL RuntimeError</b></summary>

Handled automatically by a patch in checker.py. No action required.

</details>

---

FAQ

Is this legal?
The tool queries public Discord endpoints. Using it may violate Discord's Terms of Service. You are responsible for how you use it.

Can I run multiple instances?
Yes, but they will share data/ unless you copy the directory. Use separate working directories per instance.

How fast is it?
At 100 workers with good proxies and tokens, expect 200–500 requests/second.

Does it work without proxies?
Yes, but you will be rate-limited quickly (~20 requests per IP). One or two proxies make a substantial difference.

How do I contribute?
Open a pull request. Keep changes focused, add tests where practical, and update this README.

---

Ethical Notice

Read before use.

This tool interacts with unofficial Discord endpoints. Its use may violate the Discord Terms of Service. Running it against your primary account can result in permanent suspension. Do not resell discovered usernames. Do not use it to impersonate or harass. The author accepts no liability for misuse.

Recommended practices:

· Use burner accounts for testing.
· Never share your token.
· Respect Retry-After at all times (the code does).
· Stop on repeated 401/403 responses.

---

License

MIT — see LICENSE for details.

---



Built with Python, asyncio, aiohttp, and Rich.

Star the project if it saved you time.

