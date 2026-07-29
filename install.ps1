# cgh installer for Windows PowerShell.
# Usage:
#   irm https://raw.githubusercontent.com/altikva/cgh/main/install.ps1 | iex
#
# Installs cgh (uv tool, then pipx, then pip --user) and offers to add the cgh
# command to your user PATH. For Git Bash / WSL / macOS / Linux, use install.sh.

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "  + $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host @"

   ___          _                          _
  / __\___   __| | ___  __ _ _ __ __ _ _ __ | |__
 / /  / _ \ / _``|/ _ \/ _``| '__/ _``| '_ \| '_ \
/ /__| (_) | (_| |  __/ (_| | | | (_| | |_) | | | |
\____/\___/ \__,_|\___|\__, |_|  \__,_| .__/|_| |_|
                       |___/          |_|

"@ -ForegroundColor Cyan

# --- find a Python 3.11+ ----------------------------------------------------
$py = $null
foreach ($cand in @("py", "python", "python3")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) {
    Write-Host "Error: Python 3.11+ is required but not found." -ForegroundColor Red
    Write-Host "Install Python: https://python.org/downloads/"
    exit 1
}

$pyVer = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pyOk = & $py -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)"
if ($pyOk.Trim() -ne "1") {
    Write-Host "Error: Python 3.11+ required, found $pyVer" -ForegroundColor Red
    exit 1
}
Write-Step "Python $pyVer found"

# --- install ----------------------------------------------------------------
# The PyPI package is `cgh` (not `codegraph`, an unrelated project).
# $env:CGH_PLUGINS = 1 installs the five first-party plugins in one shot:
#   $env:CGH_PLUGINS = 1; irm .../install.ps1 | iex
$spec = "cgh"
if ($env:CGH_PLUGINS) {
    $spec = "cgh[plugins]"
    Write-Step "Including the first-party plugins (cgh[plugins])"
}
$installer = ""
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Step "Installing with uv tool"
    uv tool install $spec; $installer = "uv"
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Step "Installing with pipx"
    pipx install $spec; $installer = "pipx"
} else {
    Write-Warn "uv and pipx not found, using pip --user"
    & $py -m pip install --user $spec; $installer = "pip"
}

# --- verify, and offer to fix PATH ------------------------------------------
if (Get-Command cgh -ErrorAction SilentlyContinue) {
    Write-Host "`ncgh installed and on your PATH." -ForegroundColor Green
    cgh --version
} else {
    Write-Warn "cgh installed, but the command is not on your PATH yet."
    $ans = Read-Host "Add cgh to your PATH now? [Y/n]"
    if ($ans -match '^[Nn]') {
        Write-Host "Skipped. You can always run it as: $py -m cgh" -ForegroundColor Cyan
    } else {
        if ($installer -eq "uv") {
            uv tool update-shell
        } elseif ($installer -eq "pipx") {
            pipx ensurepath
        } else {
            $scripts = (& $py -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            if ($userPath -notlike "*$scripts*") {
                [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
                Write-Step "Added $scripts to your user PATH"
            } else {
                Write-Host "  Already on your user PATH." -ForegroundColor DarkGray
            }
        }
        Write-Host "Open a new terminal, then: cgh --version" -ForegroundColor Cyan
    }
}

Write-Host "`nQuick start:" -ForegroundColor Cyan
Write-Host "  cd your-project"
Write-Host "  cgh init        # or: $py -m cgh init"
Write-Host "  cgh index"
Write-Host "  cgh stats"
