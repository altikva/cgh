# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: PATH helper behind `cgh ensurepath`. Finds the directory where
#              pip dropped the cgh executable, detects the environment (Git
#              Bash, WSL, Linux, macOS, native Windows), and adds that dir to
#              the right shell profile. Pure, testable functions; the CLI in
#              cli/commands_ensurepath.py wires real env / home and prompts.

from __future__ import annotations

import os
import re
import sys
import sysconfig
from pathlib import Path

MARKER = "# added by cgh ensurepath"


def scripts_dir() -> str:
    """Directory where pip installs console scripts (cgh, cgh.exe) for the
    running interpreter."""
    return sysconfig.get_path("scripts")


def _split_path(path_env: str) -> list[str]:
    return [p for p in path_env.split(os.pathsep) if p]


def is_on_path(target: str, path_env: str | None = None) -> bool:
    """True if ``target`` is already one of the PATH entries."""
    path_env = os.environ.get("PATH", "") if path_env is None else path_env
    want = os.path.normcase(os.path.normpath(target))
    return any(
        os.path.normcase(os.path.normpath(p)) == want for p in _split_path(path_env)
    )


def to_msys_path(win_path: str) -> str:
    """Convert a Windows path to the MSYS / Git Bash form. C:\\X\\Y -> /c/X/Y."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path)
    if not m:
        return win_path.replace("\\", "/")
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    return f"/{drive}/{rest}"


def detect_env(environ: dict | None = None) -> str:
    """Classify the runtime: gitbash | windows | wsl | macos | linux."""
    environ = os.environ if environ is None else environ
    if environ.get("MSYSTEM"):
        return "gitbash"
    if os.name == "nt":
        return "windows"
    try:
        if "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower():
            return "wsl"
    except OSError:
        pass
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def shell_profile(environ: dict | None = None, home: Path | str | None = None) -> Path:
    """The shell rc file to edit, based on $SHELL. Falls back to ~/.profile."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    shell = os.path.basename(environ.get("SHELL", "") or "")
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc"
    return home / ".profile"


def path_value_for(env: str, scripts: str) -> str:
    """The PATH entry to write: MSYS form under Git Bash, otherwise as-is."""
    return to_msys_path(scripts) if env == "gitbash" else scripts


def append_to_profile(profile: Path, dir_value: str) -> str:
    """Append an export line for ``dir_value`` if not already present.

    Returns "already" when the cgh block for this dir is in the file, else
    "added". Idempotent via the MARKER comment plus the dir value.
    """
    existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if MARKER in existing and dir_value in existing:
        return "already"
    line = f'export PATH="$PATH:{dir_value}"  {MARKER}'
    profile.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with profile.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}\n{line}\n")
    return "added"
