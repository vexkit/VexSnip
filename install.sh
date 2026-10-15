#!/usr/bin/env bash
# ============================================================================
#  VexUserCheck v3.1 — Installer
# ----------------------------------------------------------------------------
#  Responsibilities:
#    1. Verify Python 3.10+ is available
#    2. Create a virtual environment at .venv/
#    3. Install aiohttp + rich
#    4. Create project directories (data/, logs/, results/)
#    5. Seed empty files (proxies.txt, tokens.txt, names_to_check.txt)
#    6. Verify the installation with a smoke test
# ============================================================================

set -euo pipefail

# ── Locate project root ──────────────────────────────────────────────────────
cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"

# ── Colours (only when attached to a TTY) ────────────────────────────────────
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
else
    BOLD=''; DIM=''; ITALIC=''
    CYAN=''; MAGENTA=''; BLUE=''; GREEN=''; YELLOW=''; RED=''; GRAY=''
    RESET=''
fi

# ── Logging helpers ──────────────────────────────────────────────────────────
log_step()  { printf "${BLUE}${BOLD}»${RESET} ${BOLD}%s${RESET}\n" "$1"; }
log_ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
log_warn()  { printf "  ${YELLOW}⚠${RESET} %s\n" "$1"; }
log_fail()  { printf "  ${RED}✗${RESET} %s\n" "$1"; }
log_info()  { printf "  ${GRAY}·${RESET} %s\n" "$1"; }

die() {
    printf "\n${RED}${BOLD}[!] %s${RESET}\n" "$1"
    exit 1
}

# ── Banner ───────────────────────────────────────────────────────────────────
banner() {
    printf "\n"
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
    printf "${GRAY}   v3.1 · Installer${RESET}\n\n"
}

# ── 1. Verify Python ─────────────────────────────────────────────────────────
check_python() {
    log_step "Checking Python"

    if ! command -v python3 >/dev/null 2>&1; then
        die "python3 not found in PATH. Install Python 3.10+ and try again."
    fi

    local version
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        die "Python ${version} detected. VexUserCheck requires 3.10 or newer."
    fi

    log_ok "Python ${version}"
}

# ── 2. Create virtual environment ────────────────────────────────────────────
create_venv() {
    log_step "Virtual environment"

    if [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
        log_ok ".venv already exists"
        return 0
    fi

    if [ -d ".venv" ]; then
        log_warn ".venv exists but is broken — recreating"
        rm -rf .venv
    fi

    if ! python3 -m venv .venv; then
        die "Failed to create virtual environment. Install python3-venv and retry."
    fi

    log_ok "Created .venv/"
}

# ── 3. Install dependencies ──────────────────────────────────────────────────
install_deps() {
    log_step "Installing dependencies"

    if [ ! -f "requirements.txt" ]; then
        die "requirements.txt not found in ${PROJECT_ROOT}."
    fi

    # Upgrade pip quietly, but don't fail if it errors (offline / locked)
    .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || \
        log_warn "pip upgrade skipped (offline or permission denied)"

    if ! .venv/bin/pip install -r requirements.txt; then
        die "Failed to install dependencies. Check your network connection."
    fi

    log_ok "aiohttp + rich installed"
}

# ── 4. Create directories and seed files ─────────────────────────────────────
scaffold() {
    log_step "Creating project structure"

    mkdir -p data logs results

    # Only seed files that don't exist — never overwrite user data
    local files=(
        "data/proxies.txt"
        "data/tokens.txt"
        "data/names_to_check.txt"
        "data/progress.json:{}"
        "data/config.json:{}"
        "logs/error.txt"
        "results/hits.txt"
    )

    for entry in "${files[@]}"; do
        local path="${entry%%:*}"
        local seed="${entry#*:}"
        if [ ! -f "$path" ]; then
            if [ "$seed" = "$path" ]; then
                : > "$path"                      # empty file
            else
                printf '%s' "$seed" > "$path"    # seed content
            fi
        fi
    done

    log_ok "data/  logs/  results/"
}

# ── 5. Smoke test ────────────────────────────────────────────────────────────
smoke_test() {
    log_step "Verifying installation"

    if ! .venv/bin/python -c "import aiohttp, rich" >/dev/null 2>&1; then
        die "Smoke test failed: cannot import aiohttp / rich."
    fi
    log_ok "Imports OK"

    if [ ! -f "checker.py" ]; then
        log_warn "checker.py not found — the project may be incomplete"
    else
        if ! .venv/bin/python -m py_compile checker.py >/dev/null 2>&1; then
            die "Smoke test failed: checker.py has a syntax error."
        fi
        log_ok "checker.py compiles"
    fi
}

# ── 6. Success message ───────────────────────────────────────────────────────
success() {
    printf "\n"
    printf "${GREEN}${BOLD}═══════════════════════════════════════════════${RESET}\n"
    printf "${GREEN}${BOLD}   ✓  Installation complete${RESET}\n"
    printf "${GREEN}${BOLD}═══════════════════════════════════════════════${RESET}\n\n"

    printf "${BOLD}Next steps:${RESET}\n"
    printf "  ${CYAN}./run.sh${RESET}              ${GRAY}# interactive wizard${RESET}\n"
    printf "  ${CYAN}./run.sh -n${RESET}           ${GRAY}# use saved config${RESET}\n"
    printf "  ${CYAN}./run.sh --help${RESET}       ${GRAY}# show all options${RESET}\n\n"

    printf "${GRAY}Documentation: ${RESET}${CYAN}https://github.com/vex/VexUserCheck${RESET}\n\n"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    banner
    check_python
    create_venv
    install_deps
    scaffold
    smoke_test
    success
}

main "$@"