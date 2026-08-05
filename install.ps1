# cgh installer for Windows PowerShell.
# Usage:
#   irm https://raw.githubusercontent.com/altikva/cgh/main/install.ps1 | iex
#
# Installs cgh (uv tool, then pipx, then pip --user) and offers to add the cgh
# command to your user PATH. For Git Bash / WSL / macOS / Linux, use install.sh.
#
# Behind a corporate network:
#   $env:CGH_INDEX_URL = "https://nexus.corp/repository/pypi/simple"
#   $env:CGH_TRUSTED_HOST = "nexus.corp"     # self-signed TLS
#   $env:CGH_TIMEOUT = 300                   # slow or flaky link
# Each installer is tried in turn, so one failing does not end the run.

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

# --- network posture --------------------------------------------------------
# One place to say "the index is over there" and "the link is slow", honored
# by uv, pipx and pip alike through their environment variables. An index the
# caller already exported is respected, never overwritten.
$timeout = if ($env:CGH_TIMEOUT) { $env:CGH_TIMEOUT } else { "120" }
$retries = if ($env:CGH_RETRIES) { $env:CGH_RETRIES } else { "5" }
if (-not $env:PIP_TIMEOUT) { $env:PIP_TIMEOUT = $timeout }
if (-not $env:PIP_RETRIES) { $env:PIP_RETRIES = $retries }
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = $timeout }

$index = if ($env:CGH_INDEX_URL) { $env:CGH_INDEX_URL } else { $env:PIP_INDEX_URL }
if ($index) {
    $env:PIP_INDEX_URL = $index
    # uv renamed the variable; set both so any version picks it up.
    $env:UV_INDEX_URL = $index
    $env:UV_DEFAULT_INDEX = $index
    Write-Step "Package index: $index"
}
if ($env:CGH_TRUSTED_HOST) {
    $env:PIP_TRUSTED_HOST = $env:CGH_TRUSTED_HOST
    $env:UV_INSECURE_HOST = $env:CGH_TRUSTED_HOST
    Write-Warn "TLS verification relaxed for $($env:CGH_TRUSTED_HOST)"
}

# --- install ----------------------------------------------------------------
# The PyPI package is `cgh` (not `codegraph`, an unrelated project).
# $env:CGH_PLUGINS = 1 installs the five first-party plugins in one shot:
#   $env:CGH_PLUGINS = 1; irm .../install.ps1 | iex
$spec = "cgh"
if ($env:CGH_PLUGINS) {
    $spec = "cgh[full]"
    Write-Step "Including plugins, extra parsers and precise calls (cgh[full])"
}
# Every installer gets its turn. Native commands do not throw on failure in
# PowerShell, so a failed install used to be reported as a success; the exit
# code is now what decides.
$installer = ""
function Try-Install($name, $exe, $arguments) {
    if ($installer) { return }
    if ($exe -ne $py -and -not (Get-Command $exe -ErrorAction SilentlyContinue)) { return }
    Write-Step "Installing with $name"
    $global:LASTEXITCODE = 0
    try {
        # PowerShell 7.4+ turns a non-zero native exit into a terminating
        # error under ErrorActionPreference = Stop; older versions only set
        # LASTEXITCODE. Both must fall through to the next installer.
        & $exe @arguments
    } catch {
        Write-Warn "$name failed ($($_.Exception.Message)), trying the next installer"
        return
    }
    if ($LASTEXITCODE -eq 0) {
        $script:installer = $name
    } else {
        Write-Warn "$name failed (exit $LASTEXITCODE), trying the next installer"
    }
}

Try-Install "uv" "uv" @("tool", "install", $spec)
Try-Install "pipx" "pipx" @("install", $spec)
Try-Install "pip" $py @("-m", "pip", "install", "--user",
                        "--timeout", $timeout, "--retries", $retries, $spec)

if (-not $installer) {
    Write-Host "`nEvery installer failed." -ForegroundColor Red
    Write-Host "If your network blocks PyPI, point the installer at your internal mirror:"
    Write-Host '  $env:CGH_INDEX_URL = "https://your-mirror/simple"; irm .../install.ps1 | iex'
    Write-Host 'Slow or flaky link? raise the budget: $env:CGH_TIMEOUT = 300'
    exit 1
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
