#!/usr/bin/env bash
# cgh installer for bash environments: macOS, Linux, WSL, and Git Bash (Windows).
# Usage: curl -fsSL https://raw.githubusercontent.com/altikva/cgh/main/install.sh | bash
#
# Detects the environment, installs cgh (uv tool, then pipx, then pip --user),
# and offers to add the cgh command to your PATH. On native Windows PowerShell
# or cmd, use install.ps1 instead.
#
# Behind a corporate network:
#   CGH_INDEX_URL=https://nexus.corp/repository/pypi/simple  (internal mirror)
#   CGH_TRUSTED_HOST=nexus.corp                              (self-signed TLS)
#   CGH_TIMEOUT=120 CGH_RETRIES=8                            (slow or flaky link)
# Each installer is tried in turn, so one failing does not end the run.

set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; CYAN="\033[36m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
echo '   ___          _                          _'
echo '  / __\___   __| | ___  __ _ _ __ __ _ _ __ | |__'
echo ' / /  / _ \ / _` |/ _ \/ _` |'\''__/ _` | '\''_ \| '\''_ \'
echo '/ /__| (_) | (_| |  __/ (_| | | | (_| | |_) | | | |'
echo '\____/\___/ \__,_|\___|\__, |_|  \__,_| .__/|_| |_|'
echo '                       |___/          |_|'
echo -e "${RESET}"

# --- detect the environment -------------------------------------------------
detect_env() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*) echo "gitbash" ;;
    Linux)
      if grep -qi microsoft /proc/version 2>/dev/null; then echo "wsl"; else echo "linux"; fi ;;
    Darwin) echo "macos" ;;
    *) echo "unknown" ;;
  esac
}
ENVIRON="$(detect_env)"
echo -e "${BOLD}Installing cgh${RESET} ${CYAN}(${ENVIRON})${RESET}\n"

# --- find a Python 3.11+ ----------------------------------------------------
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo -e "${RED}Error: Python 3.11+ is required but not found.${RESET}"
  echo "Install Python: https://python.org/downloads/"
  exit 1
fi

PY_VERSION=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"; then
  echo -e "${RED}Error: Python 3.11+ required, found $PY_VERSION${RESET}"
  exit 1
fi
echo -e "${GREEN}+${RESET} Python $PY_VERSION found"

# --- network posture --------------------------------------------------------
# One place to say "the index is over there" and "the link is slow", honored
# by uv, pipx and pip alike through their environment variables. An index the
# caller already exported is respected, never overwritten.
TIMEOUT="${CGH_TIMEOUT:-120}"
RETRIES="${CGH_RETRIES:-5}"
export PIP_TIMEOUT="${PIP_TIMEOUT:-$TIMEOUT}"
export PIP_RETRIES="${PIP_RETRIES:-$RETRIES}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-$TIMEOUT}"

INDEX="${CGH_INDEX_URL:-${PIP_INDEX_URL:-}}"
if [ -n "$INDEX" ]; then
  export PIP_INDEX_URL="$INDEX"
  # uv renamed the variable; export both so any version picks it up.
  export UV_INDEX_URL="$INDEX"
  export UV_DEFAULT_INDEX="$INDEX"
  echo -e "${GREEN}+${RESET} Package index: ${CYAN}${INDEX}${RESET}"
fi
if [ -n "${CGH_TRUSTED_HOST:-}" ]; then
  export PIP_TRUSTED_HOST="$CGH_TRUSTED_HOST"
  export UV_INSECURE_HOST="$CGH_TRUSTED_HOST"
  echo -e "${YELLOW}!${RESET} TLS verification relaxed for ${CGH_TRUSTED_HOST}"
fi

# --- install ----------------------------------------------------------------
# The PyPI package is `cgh` (not `codegraph`, which is an unrelated project).
# CGH_PLUGINS=1 installs the five first-party plugins in the same shot:
#   curl -fsSL .../install.sh | CGH_PLUGINS=1 bash
SPEC="cgh"
if [ -n "${CGH_PLUGINS:-}" ]; then
  SPEC="cgh[full]"
  echo -e "${GREEN}+${RESET} Including plugins, extra parsers and precise calls (cgh[full])"
fi
# Every installer gets its turn: a network hiccup on uv used to abort the
# script (set -e) instead of falling through to pipx and pip.
INSTALLER=""
attempt() {
  local name="$1"; shift
  command -v "$1" >/dev/null 2>&1 || [ "$1" = "$PY" ] || return 1
  echo -e "${GREEN}+${RESET} Installing with ${name}"
  if "$@"; then
    INSTALLER="$name"
    return 0
  fi
  echo -e "${YELLOW}!${RESET} ${name} failed, trying the next installer"
  return 1
}

attempt uv uv tool install "$SPEC" \
  || attempt pipx pipx install "$SPEC" \
  || attempt pip "$PY" -m pip install --user \
       --timeout "$TIMEOUT" --retries "$RETRIES" "$SPEC" \
  || true

if [ -z "$INSTALLER" ]; then
  echo -e "\n${RED}${BOLD}Every installer failed.${RESET}"
  echo -e "If your network blocks PyPI, point the installer at your internal mirror:"
  echo -e "  ${BOLD}curl -fsSL .../install.sh | CGH_INDEX_URL=https://your-mirror/simple bash${RESET}"
  echo -e "Slow or flaky link? raise the budget: ${BOLD}CGH_TIMEOUT=300 CGH_RETRIES=10${RESET}"
  exit 1
fi

# --- verify, and offer to fix PATH ------------------------------------------
if command -v cgh >/dev/null 2>&1; then
  echo -e "\n${GREEN}${BOLD}cgh installed and on your PATH.${RESET}"
  cgh --version || true
else
  echo -e "\n${YELLOW}cgh installed, but the command is not on your PATH yet.${RESET}"
  ADD="y"
  if [ -e /dev/tty ]; then
    printf "Add cgh to your PATH now? [Y/n] "
    read -r ADD < /dev/tty || ADD="y"
  fi
  case "${ADD:-y}" in
    [Nn]*)
      echo -e "${CYAN}Skipped.${RESET} You can always run it as: ${BOLD}$PY -m cgh${RESET}"
      ;;
    *)
      case "$INSTALLER" in
        uv)   uv tool update-shell || true ;;
        pipx) pipx ensurepath || true ;;
        *)    "$PY" -m cgh ensurepath --yes || true ;;
      esac
      echo -e "${CYAN}Open a new terminal (or source your shell profile), then:${RESET} ${BOLD}cgh --version${RESET}"
      ;;
  esac
fi

echo -e "\n${CYAN}${BOLD}Quick start:${RESET}"
echo "  cd your-project"
echo "  cgh init        # or: $PY -m cgh init"
echo "  cgh index"
echo "  cgh stats"
