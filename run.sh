#!/usr/bin/env bash
# ============================================================================
#  VexUserCheck v3.1 — Professional Launcher
# ----------------------------------------------------------------------------
#  Thin wrapper around `checker.py`. Responsibilities:
#    1. Verify Python venv and dependencies
#    2. Auto-run install.sh if anything is missing
#    3. Hand off to the Python wizard / scanner
#
#  The full welcome screen, wizard, and live UI are handled by Python
#  (ui.welcome + wizard.setup_wizard). This script only prepares the
#  environment and shows a brief startup animation.
# ============================================================================

set -euo pipefail

# ── Locate project root ──────────────────────────────────────────────────────
cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"

# ── Colours (fall back to plain when not a TTY) ──────────────────────────────
if [ -t 1 ]; then
    BOLD='\033[1m'
    DIM='\033[2m'
    ITALIC='\033[3m'
    CYAN='\033[36m'
    MAGENTA='\033[35m'
    BLUE='\033[34m'
    GREEN='\033[32m'
    YELLOW='\033[33m'
    RED='\033[31m'
    GRAY='\033[90m'
    RESET='\033[0m'
    CLEAR='\r\033[K'
    IS_TTY=1
else
    BOLD=''; DIM=''; ITALIC=''
    CYAN=''; MAGENTA=''; BLUE=''; GREEN=''; YELLOW=''; RED=''; GRAY=''
    RESET=''; CLEAR=''
    IS_TTY=0
fi

# ── Help / version shortcuts ─────────────────────────────────────────────────
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<EOF

${BOLD}${CYAN}VexUserCheck${RESET} ${GRAY}·${RESET} ${ITALIC}Async Discord Username Availability Checker${RESET}

${BOLD}USAGE${RESET}
  ./run.sh [options]

${BOLD}OPTIONS${RESET}
  ${CYAN}-n, --no-wizard${RESET}         Skip wizard; use saved config
  ${CYAN}    --resume-from-hits${RESET}    Skip names already in results/hits.txt
  ${CYAN}    --no-resume-progress${RESET}  Ignore data/progress.json
  ${CYAN}    --no-sound${RESET}            Disable sound on hit
  ${CYAN}    --length N${RESET}            Username length (used with -n)
  ${CYAN}    --count N${RESET}             Number of usernames to generate
  ${CYAN}    --webhook-min-hits N${RESET}  Webhook batch threshold
  ${CYAN}-d, --debug${RESET}              Enable verbose per-request output
  ${CYAN}    --version${RESET}             Print version
  ${CYAN}-h, --help${RESET}                Show this help

${BOLD}EXAMPLES${RESET}
  ${GRAY}#${RESET} full interactive wizard
  ./run.sh

  ${GRAY}#${RESET} use saved config (no questions)
  ./run.sh -n

  ${GRAY}#${RESET} generate 4-char usernames and scan
  ./run.sh -n --length 4 --count 200000

  ${GRAY}#${RESET} skip names already found in previous runs
  ./run.sh -n --resume-from-hits

${GRAY}Docs:${RESET} ${CYAN}https://github.com/vex/VexUserCheck${RESET}
EOF
    exit 0
fi

# ── Startup animation (TTY only) ─────────────────────────────────────────────
if [ "$IS_TTY" = "1" ]; then
    clear

    # Wordmark
    printf "${CYAN}${BOLD}"
    cat <<'EOF'
 ██╗   ██╗███████╗██╗  ██╗
 ██║   ██║██╔════╝╚██╗██╔╝
 ██║   ██║█████╗   ╚███╔╝
 ╚██╗ ██╔╝██╔══╝   ██╔██╗
  ╚████╔╝ ███████╗██╔╝ ██╗
   ╚═══╝  ╚══════╝╚═╝  ╚═╝
EOF
    printf "${RESET}\n"

    printf "${MAGENTA}${BOLD}      U S E R   C H E C K${RESET}\n"
    printf "${GRAY}${ITALIC}   Async Discord Username Availability Checker${RESET}\n"
    printf "${GRAY}   v3.1 · aiohttp · asyncio${RESET}\n\n"

    # Small loader (skipped if user passed -h)
    printf "${BLUE}${BOLD}»${RESET} ${BOLD}Preparing environment${RESET}"
    frames=("." ".." "..." "....")
    for i in $(seq 0 9); do
        printf "${CLEAR}${BLUE}${BOLD}»${RESET} ${BOLD}Preparing environment%s${RESET}" "${frames[$((i % 4))]}"
        sleep 0.06
    done
    printf "${CLEAR}"
fi

# ── Verify Python 3 ──────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    printf "${RED}${BOLD}[!]${RESET} ${BOLD}python3${RESET} not found in PATH.\n"
    printf "${GRAY}    Install Python 3.10+ and try again.${RESET}\n"
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    printf "${RED}${BOLD}[!]${RESET} Python ${PYTHON_VERSION} is too old. Requires ${BOLD}3.10+${RESET}.\n"
    exit 1
fi

# ── Verify venv ──────────────────────────────────────────────────────────────
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    printf "${YELLOW}${BOLD}[+]${RESET} ${BOLD}.venv${RESET} missing — running ${CYAN}install.sh${RESET}...\n"
    if [ ! -f "${PROJECT_ROOT}/install.sh" ]; then
        printf "${RED}${BOLD}[!]${RESET} install.sh not found. Aborting.\n"
        exit 1
    fi
    bash "${PROJECT_ROOT}/install.sh" || {
        printf "${RED}${BOLD}[!]${RESET} Installation failed.\n"
        exit 1
    }
fi

# ── Verify dependencies ──────────────────────────────────────────────────────
if ! "$VENV_PY" -c "import aiohttp, rich" >/dev/null 2>&1; then
    printf "${YELLOW}${BOLD}[+]${RESET} Missing dependencies — re-running ${CYAN}install.sh${RESET}...\n"
    bash "${PROJECT_ROOT}/install.sh" || {
        printf "${RED}${BOLD}[!]${RESET} Installation failed.\n"
        exit 1
    }
fi

# ── Final clean line before handoff ──────────────────────────────────────────
if [ "$IS_TTY" = "1" ]; then
    printf "${GREEN}${BOLD}●${RESET} ${BOLD}Ready.${RESET} ${GRAY}Handing off to Python...${RESET}\n"
    sleep 0.15
fi

# ── Launch Python (replaces this process) ────────────────────────────────────
exec "$VENV_PY" "${PROJECT_ROOT}/checker.py" "$@"