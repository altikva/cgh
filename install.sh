#!/usr/bin/env bash
# codegraph installer — works on macOS, Linux, and WSL
# Usage: curl -fsSL https://raw.githubusercontent.com/altikva/codegraph/main/install.sh | bash

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
echo '   ___          _                          _'
echo '  / __\___   __| | ___  __ _ _ __ __ _ _ __ | |__'
echo ' / /  / _ \ / _` |/ _ \/ _` |'\''__/ _` | '\''_ \| '\''_ \'
echo '/ /__| (_) | (_| |  __/ (_| | | | (_| | |_) | | | |'
echo '\____/\___/ \__,_|\___|\__, |_|  \__,_| .__/|_| |_|'
echo '                       |___/          |_|'
echo -e "${RESET}"
echo -e "${BOLD}Installing codegraph...${RESET}\n"

# Check Python 3.11+
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo -e "${RED}Error: Python 3.11+ is required but not found.${RESET}"
    echo "Install Python: https://python.org/downloads/"
    exit 1
fi

PY_VERSION=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PY -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PY -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo -e "${RED}Error: Python 3.11+ required, found $PY_VERSION${RESET}"
    exit 1
fi

echo -e "${GREEN}+${RESET} Python $PY_VERSION found"

# Prefer pipx for isolated install, fall back to pip
if command -v pipx &>/dev/null; then
    echo -e "${GREEN}+${RESET} Using pipx for isolated install"
    pipx install codegraph 2>/dev/null || pipx install git+https://github.com/altikva/codegraph.git
elif command -v uv &>/dev/null; then
    echo -e "${GREEN}+${RESET} Using uv for install"
    uv tool install codegraph 2>/dev/null || uv pip install codegraph
else
    echo -e "${YELLOW}!${RESET} pipx not found, using pip (consider: pip install pipx)"
    $PY -m pip install --user codegraph 2>/dev/null || $PY -m pip install --user git+https://github.com/altikva/codegraph.git
fi

# Verify
if command -v codegraph &>/dev/null; then
    echo -e "\n${GREEN}${BOLD}codegraph installed successfully!${RESET}"
    codegraph --version
    echo -e "\n${CYAN}Quick start:${RESET}"
    echo "  cd your-project"
    echo "  codegraph init"
    echo "  codegraph index"
    echo "  codegraph stats"
else
    echo -e "\n${YELLOW}Installed but 'codegraph' not in PATH.${RESET}"
    echo "Try: $PY -m codegraph --version"
    echo "Or add ~/.local/bin to your PATH"
fi
