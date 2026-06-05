#!/usr/bin/env bash
# cgh installer for bash environments: macOS, Linux, WSL, and Git Bash (Windows).
# Usage: curl -fsSL https://raw.githubusercontent.com/altikva/cgh/main/install.sh | bash
#
# Detects the environment, installs cgh (uv tool, then pipx, then pip --user),
# and offers to add the cgh command to your PATH. On native Windows PowerShell
# or cmd, use install.ps1 instead.

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

# --- install ----------------------------------------------------------------
# The PyPI package is `cgh` (not `codegraph`, which is an unrelated project).
INSTALLER=""
if command -v uv >/dev/null 2>&1; then
  echo -e "${GREEN}+${RESET} Installing with uv tool"
  uv tool install cgh && INSTALLER="uv"
elif command -v pipx >/dev/null 2>&1; then
  echo -e "${GREEN}+${RESET} Installing with pipx"
  pipx install cgh && INSTALLER="pipx"
else
  echo -e "${YELLOW}!${RESET} uv and pipx not found, using pip --user"
  "$PY" -m pip install --user cgh && INSTALLER="pip"
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
