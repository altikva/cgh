# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Single-writer lock for `cgh serve`. Prevents graph DB
#              write-lock contention when multiple Claude Code sessions
#              or reloads try to start competing MCP servers for the
#              same repo.

from __future__ import annotations

import atexit
import os
import signal
import subprocess
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
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists and belongs to someone else, still alive. It is not
        # our child, so we cannot reap it and its zombie state is not our
        # concern; treat it as alive.
        return True
    except OSError:
        return False
    # kill(pid, 0) also succeeds for a ZOMBIE: a process that has exited but
    # not yet been reaped still occupies the table. A zombie owner is dead,
    # not a running server, so a stale owner.pid pointing at one must not read
    # as alive, or every later `cgh serve` refuses to start ("another owner is
    # running") and the repo wedges until the pidfiles are deleted by hand.
    return _process_state(pid) != "Z"


def _parse_proc_stat_state(data: bytes) -> str | None:
    """The one-char process state from the contents of a Linux
    ``/proc/<pid>/stat`` line. Field 2 (comm) is wrapped in parentheses and
    may itself contain spaces and parens, so scan from the LAST ``)``; the
    state is the first non-space character after it. Returns None if the line
    does not parse."""
    rparen = data.rfind(b")")
    if rparen == -1:
        return None
    rest = data[rparen + 1 :].lstrip()
    return chr(rest[0]) if rest else None


def _process_state(pid: int) -> str | None:
    """Best-effort single-character process state ('R', 'S', 'D', 'Z', 'T',
    ...), or None when it cannot be determined on this platform. Linux reads
    ``/proc/<pid>/stat`` directly; elsewhere (macOS, BSD) it asks ``ps``, whose
    state code also leads with 'Z' for a zombie. A None result means "unknown",
    so callers must not treat it as proof of death."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return _parse_proc_stat_state(fh.read())
    except FileNotFoundError:
        pass  # no procfs (macOS/BSD), or the pid vanished; fall through to ps
    except OSError:
        return None
    try:
        out = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = (out.stdout or "").strip()
    return state[0] if state else None


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


def terminate(pid: int, graceful_timeout: float = 5.0) -> None:
    """Stop a process, cross-platform.

    POSIX: SIGTERM, wait up to ``graceful_timeout`` for a clean exit (so the
    target runs its atexit cleanup), then SIGKILL if it is still alive.
    Windows: TerminateProcess via the Win32 API. There is no graceful signal
    on Windows (SIGTERM is already a hard kill there, and SIGKILL does not
    exist), so the wait does not apply.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        _windows_terminate(pid)
        return
    import time

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + graceful_timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        pass


def _windows_terminate(pid: int) -> None:
    import ctypes

    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


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
      - (False, pid), another live cgh serve holds it (pid may be None if a
        rival is still mid-claim)

    The claim is atomic: our pid is staged in a per-process temp file and then
    hard-linked into place with ``os.link``, which fails if the slot is already
    taken. Exactly one racing owner wins the link, and the pidfile is never
    observed empty (it appears fully written), so a rival cannot misread a
    mid-write file as stale and clobber it. The previous check-then-write let
    two owners spawned at once both pass the liveness check and both write their
    pid, so the loser of the graph DB write lock could leave owner.pid pointing
    at its own now-dead process while the winner kept serving. A stale pidfile
    (its process no longer alive) is taken over by removing it and retrying the
    link, with the link as the arbiter so a live holder is never clobbered.
    """
    path = _pidfile_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    mypid = os.getpid()

    # Stage our pid, fully written, in a per-process temp on the same directory
    # (so the hard link stays within one filesystem), then link it into place.
    staged = path.with_name(f"{path.name}.{mypid}")
    try:
        staged.write_text(str(mypid) + "\n", encoding="utf-8")
        # Bounded retries so a rival churning a stale file cannot spin us forever.
        for _ in range(100):
            try:
                os.link(staged, path)
            except FileExistsError:
                existing = read_existing_pid(repo_root)
                if (
                    existing is not None
                    and existing != mypid
                    and _is_process_alive(existing)
                ):
                    return False, existing
                # Stale (dead holder or our own leftover): drop it and retry the
                # link. The link is the arbiter, so if a rival relinks a live pid
                # first, the next pass reads it and yields rather than removing it.
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            else:
                atexit.register(release, repo_root)
                # Best-effort: release on SIGTERM too (atexit may not fire).
                try:
                    signal.signal(signal.SIGTERM, _sigterm_handler_factory(repo_root))
                except (ValueError, OSError):
                    # Signal registration can fail in non-main threads; non-fatal.
                    pass
                return True, None

        # A rival kept recreating a stale file faster than we could take it over.
        # Report it as contended rather than loop forever.
        return False, read_existing_pid(repo_root)
    finally:
        # Drop the temp. If the link succeeded, `path` keeps the inode (and our
        # pid) with its own link count, so removing the temp is safe.
        try:
            staged.unlink()
        except FileNotFoundError:
            pass


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
