#!/usr/bin/env bash
# cgh installer for bash environments: macOS, Linux, WSL, and Git Bash (Windows).
# Usage: curl -fsSL https://raw.githubusercontent.com/altikva/cgh/main/install.sh | bash
#
# Tries three ways to install cgh, in order, stopping at the first that works:
#   1. Python  uv tool, then pipx, then pip --user   (the full PyPI package)
#   2. Node    npm install -g @altikva/cgh           (wrapper that fetches the binary)
#   3. Binary  download the prebuilt cgh for this platform from the latest
#              GitHub Release, verify its SHA-256, and drop it on your PATH
# then offers to add the cgh command to your PATH. On native Windows PowerShell
# or cmd, use install.ps1 instead.
#
# Behind a corporate network (applies to the Python installers):
#   CGH_INDEX_URL=https://nexus.corp/repository/pypi/simple  (internal mirror)
#   CGH_TRUSTED_HOST=nexus.corp                              (self-signed TLS)
#   CGH_TIMEOUT=120 CGH_RETRIES=8                            (slow or flaky link)
#
# Binary-fallback knobs:
#   CGH_VARIANT=egress   fetch the egress build (adds the model-calling plugins);
#                        the default is the sealed, egress-free build
#   CGH_DOWNLOAD_BASE    override the release download base (air-gapped mirror)
#   CGH_BIN_DIR          where to place the downloaded binary (default ~/.local/bin)

set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; CYAN="\033[36m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"

ok()   { echo -e "${GREEN}+${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
err()  { echo -e "${RED}$*${RESET}"; }

echo -e "${CYAN}${BOLD}"
echo '   ___          _                          _'
echo '  / __\___   __| | ___  __ _ _ __ __ _ _ __ | |__'
echo ' / /  / _ \ / _` |/ _ \/ _` |'\''__/ _` | '\''_ \| '\''_ \'
echo '/ /__| (_) | (_| |  __/ (_| | | | (_| | |_) | | | |'
echo '\____/\___/ \__,_|\___|\__, |_|  \__,_| .__/|_| |_|'
echo '                       |___/          |_|'
echo -e "${RESET}"

# --- small helpers ----------------------------------------------------------
# fetch <url> <dest>: download with curl or wget, whichever is present.
fetch() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"
  else return 1
  fi
}

# sha256_of <file>: print the hex digest, or fail if no tool is available.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else return 1
  fi
}

# detect_target: map this platform to a release asset target, empty if none.
# These mirror the release matrix (packaging/build-binary.sh). Intel macOS is
# absent on purpose: the hosted Intel-mac runner was retired and PyInstaller
# cannot cross-compile it, so Intel-mac users get the Python route instead.
detect_target() {
  local os arch
  os="$(uname -s 2>/dev/null || echo)"; arch="$(uname -m 2>/dev/null || echo)"
  case "$os" in
    Darwin) case "$arch" in arm64|aarch64) echo "macos-arm64" ;; *) echo "" ;; esac ;;
    Linux)  case "$arch" in
              x86_64|amd64)  echo "linux-x64" ;;
              aarch64|arm64) echo "linux-arm64" ;;
              *) echo "" ;;
            esac ;;
    *) echo "" ;;
  esac
}

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

# --- find a Python 3.11+ (optional: the chain falls back to Node and binary) --
PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
fi
PY_OK=""
if [ -n "$PY" ] && "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
  PY_OK=1
  PY_VERSION="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  ok "Python $PY_VERSION found"
elif [ -n "$PY" ]; then
  warn "Python found but older than 3.11; will try Node, then the prebuilt binary"
else
  warn "No Python 3.11+ found; will try Node, then the prebuilt binary"
fi

# --- network posture (Python installers) ------------------------------------
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
  ok "Package index: ${CYAN}${INDEX}${RESET}"
fi
if [ -n "${CGH_TRUSTED_HOST:-}" ]; then
  export PIP_TRUSTED_HOST="$CGH_TRUSTED_HOST"
  export UV_INSECURE_HOST="$CGH_TRUSTED_HOST"
  warn "TLS verification relaxed for ${CGH_TRUSTED_HOST}"
fi

# --- what to install --------------------------------------------------------
# The PyPI package is `cgh` (not `codegraph`, which is an unrelated project).
# CGH_PLUGINS=1 installs the first-party plugins in the same shot:
#   curl -fsSL .../install.sh | CGH_PLUGINS=1 bash
SPEC="cgh"
if [ -n "${CGH_PLUGINS:-}" ]; then
  SPEC="cgh[full]"
  ok "Including plugins, extra parsers and precise calls (cgh[full])"
fi

INSTALLER=""   # granular tool that won: uv / pipx / pip / npm / binary
BIN_PATH=""    # set when the binary route places cgh directly

# --- stage 1: Python --------------------------------------------------------
# Every installer gets its turn: a network hiccup on uv used to abort the
# script (set -e) instead of falling through to pipx and pip.
attempt() {
  local name="$1"; shift
  command -v "$1" >/dev/null 2>&1 || [ "$1" = "$PY" ] || return 1
  ok "Installing with ${name}"
  if "$@"; then
    INSTALLER="$name"
    return 0
  fi
  warn "${name} failed, trying the next installer"
  return 1
}

if [ -n "$PY_OK" ]; then
  attempt uv uv tool install "$SPEC" \
    || attempt pipx pipx install "$SPEC" \
    || attempt pip "$PY" -m pip install --user \
         --timeout "$TIMEOUT" --retries "$RETRIES" "$SPEC" \
    || true
  if [ -z "$INSTALLER" ]; then
    warn "Python install did not succeed. Behind a mirror? re-run with CGH_INDEX_URL=... . Falling back to Node and the binary."
  fi
fi

# --- stage 2: Node ----------------------------------------------------------
# The npm wrapper is a thin launcher that downloads the matching binary on
# first run, so plugins/extras selection (CGH_PLUGINS) does not apply here.
if [ -z "$INSTALLER" ] && command -v npm >/dev/null 2>&1; then
  echo
  ok "Installing with npm (@altikva/cgh)"
  if npm install -g @altikva/cgh; then
    INSTALLER="npm"
  else
    warn "npm failed, trying the prebuilt binary"
  fi
fi

# --- stage 3: prebuilt binary -----------------------------------------------
if [ -z "$INSTALLER" ]; then
  TARGET="$(detect_target)"
  if [ -z "$TARGET" ]; then
    echo
    err "No install path worked, and there is no prebuilt binary for $(uname -s 2>/dev/null)/$(uname -m 2>/dev/null)."
    case "$ENVIRON" in
      macos)   echo -e "Intel Macs have no prebuilt binary. Install with Python: ${BOLD}uvx cgh${RESET} (or ${BOLD}pip install cgh${RESET})." ;;
      gitbash) echo -e "On Windows, use ${BOLD}install.ps1${RESET} in PowerShell, or install with Python: ${BOLD}uvx cgh${RESET}." ;;
      *)       echo -e "Install with Python instead: ${BOLD}uvx cgh${RESET} (or ${BOLD}pip install cgh${RESET})." ;;
    esac
    exit 1
  fi

  VARIANT="sealed"
  if [ "${CGH_VARIANT:-}" = "egress" ] || [ "${CGH_EGRESS:-}" = "1" ] || [ "${CGH_EGRESS:-}" = "true" ]; then
    VARIANT="egress"
  fi
  PREFIX="cgh"; [ "$VARIANT" = "egress" ] && PREFIX="cgh-egress"
  if [ -n "${CGH_PLUGINS:-}" ] && [ "$VARIANT" = "sealed" ]; then
    warn "The sealed binary bundles the local-only plugins (pii, classify). For the model-calling ones set CGH_VARIANT=egress."
  fi
  ASSET="${PREFIX}-${TARGET}"
  BASE="${CGH_DOWNLOAD_BASE:-https://github.com/altikva/cgh/releases/latest/download}"
  URL="${BASE}/${ASSET}"
  BIN_DIR="${CGH_BIN_DIR:-$HOME/.local/bin}"

  echo
  ok "Downloading the ${VARIANT} binary for ${TARGET}"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  if ! fetch "$URL" "$TMP/cgh"; then
    err "Download failed: $URL"
    echo -e "Air-gapped? point CGH_DOWNLOAD_BASE at a mirror, or install with Python: ${BOLD}uvx cgh${RESET}."
    exit 1
  fi
  if ! fetch "$URL.sha256" "$TMP/cgh.sha256"; then
    err "Checksum download failed: $URL.sha256"
    exit 1
  fi
  WANT="$(awk '{print $1}' "$TMP/cgh.sha256" | head -1)"
  GOT="$(sha256_of "$TMP/cgh" || true)"
  if [ -z "$GOT" ]; then
    err "No sha256 tool (sha256sum or shasum) available to verify the download."
    exit 1
  fi
  if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
    err "Checksum mismatch for ${ASSET} - refusing to install."
    echo "  expected $WANT"
    echo "  got      $GOT"
    exit 1
  fi
  ok "SHA-256 verified"
  mkdir -p "$BIN_DIR"
  mv "$TMP/cgh" "$BIN_DIR/cgh"
  chmod +x "$BIN_DIR/cgh"
  INSTALLER="binary"
  BIN_PATH="$BIN_DIR/cgh"
  ok "Installed to ${BIN_PATH}"
fi

# --- verify, and offer to fix PATH ------------------------------------------
fix_path() {
  case "$INSTALLER" in
    uv)     uv tool update-shell || true ;;
    pipx)   pipx ensurepath || true ;;
    pip)    "$PY" -m cgh ensurepath --yes || true ;;
    binary) "$BIN_PATH" ensurepath --yes || true ;;
    npm)    warn "Ensure your npm global bin is on PATH: $(npm prefix -g 2>/dev/null)/bin" ;;
  esac
}

if command -v cgh >/dev/null 2>&1; then
  echo -e "\n${GREEN}${BOLD}cgh installed and on your PATH.${RESET}"
  cgh --version || true
else
  if [ "$INSTALLER" = "binary" ]; then
    echo -e "\n${YELLOW}cgh installed at ${BIN_PATH}, but its directory is not on your PATH yet.${RESET}"
  else
    echo -e "\n${YELLOW}cgh installed, but the command is not on your PATH yet.${RESET}"
  fi
  ADD="y"
  if [ -e /dev/tty ]; then
    printf "Add cgh to your PATH now? [Y/n] "
    read -r ADD < /dev/tty || ADD="y"
  fi
  case "${ADD:-y}" in
    [Nn]*)
      if [ "$INSTALLER" = "binary" ]; then
        echo -e "${CYAN}Skipped.${RESET} Run it by full path: ${BOLD}${BIN_PATH}${RESET}"
      elif [ -n "$PY" ]; then
        echo -e "${CYAN}Skipped.${RESET} You can always run it as: ${BOLD}$PY -m cgh${RESET}"
      else
        echo -e "${CYAN}Skipped.${RESET}"
      fi
      ;;
    *)
      fix_path
      echo -e "${CYAN}Open a new terminal (or source your shell profile), then:${RESET} ${BOLD}cgh --version${RESET}"
      ;;
  esac
fi

echo -e "\n${CYAN}${BOLD}Quick start:${RESET}"
echo "  cd your-project"
if [ -n "$PY" ]; then
  echo "  cgh init        # or: $PY -m cgh init"
else
  echo "  cgh init"
fi
echo "  cgh index"
echo "  cgh stats"
