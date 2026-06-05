# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Single-writer lock for `cgh serve`. Prevents Kuzu
#              write-lock contention when multiple Claude Code sessions
#              or reloads try to start competing MCP servers for the
#              same repo.

from __future__ import annotations

import atexit
import os
import signal
from pathlib import Path

_PID_FILE = "server.pid"


def _pidfile_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _PID_FILE


def process_alive(pid: int) -> bool:
    """True if a process with this pid is running. Cross-platform.

    POSIX uses ``os.kill(pid, 0)``, a no-op probe. That is unsafe on
    Windows: ``os.kill`` there sends TerminateProcess for any signal other
    than the CTRL events, so a "0" probe would kill the process being
    checked. On Windows we query the process via OpenProcess instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists and belongs to someone else, still alive.
        return True
    except OSError:
        return False


def _windows_process_alive(pid: int) -> bool:
    """Liveness via the Win32 API, never via os.kill (which would terminate)."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return True  # couldn't read the exit code, assume alive
    finally:
        kernel32.CloseHandle(handle)


# Back-compat alias for the in-module caller.
_is_process_alive = process_alive


def read_existing_pid(repo_root: str | Path) -> int | None:
    """Return the PID recorded in the pidfile, or None if missing/invalid."""
    path = _pidfile_path(repo_root)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw)
        if pid > 0:
            return pid
    except (ValueError, OSError):
        pass
    return None


def acquire(repo_root: str | Path) -> tuple[bool, int | None]:
    """
    Try to claim the single-writer slot for this repo.
    Returns (acquired, other_pid):
      - (True, None), we now own the pidfile
      - (False, pid), another live cgh serve holds it
    Stale pidfiles (process no longer alive) are overwritten.
    """
    path = _pidfile_path(repo_root)
    existing = read_existing_pid(repo_root)
    if existing is not None and existing != os.getpid() and _is_process_alive(existing):
        return False, existing

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    atexit.register(release, repo_root)

    # Best-effort: release on SIGTERM too (atexit may not fire).
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler_factory(repo_root))
    except (ValueError, OSError):
        # Signal registration can fail in non-main threads; non-fatal.
        pass

    return True, None


def release(repo_root: str | Path) -> None:
    """Remove our pidfile if we still own it. No-op otherwise."""
    path = _pidfile_path(repo_root)
    try:
        pid = read_existing_pid(repo_root)
        if pid == os.getpid():
            path.unlink(missing_ok=True)
    except Exception:
        pass


def _sigterm_handler_factory(repo_root: str | Path):
    def _handler(signum, frame):
        release(repo_root)
        # Re-raise default behavior so the process actually exits.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    return _handler
