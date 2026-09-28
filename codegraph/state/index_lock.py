# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: One index run per repo at a time. Every whole-repo index (full or
#              incremental, from init, `cgh index`, an owner starting up or an
#              MCP scan) holds `.codegraph/index.lock`, which records the
#              holder's pid. A second run, from another process or another
#              thread, finds it held and raises IndexBusy instead of indexing
#              the same repo in parallel. Runs nest within one thread, so an
#              incremental pass that falls back to a full index keeps its lock.
#              holder() reports the running index for scan_status.

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Generator
from pathlib import Path

_LOCK_FILE = "index.lock"

# Per resolved repo root: a reentrant lock that keeps a second thread of this
# process out, and how deep the owning thread is nested (the file is taken on
# the outermost entry and released on the last exit).
_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DEPTH: dict[str, int] = {}


class IndexBusy(RuntimeError):
    """Another index run holds this repo. A RuntimeError so callers that already
    treat an index failure as "skipped" (the owner's startup reindex) do."""

    def __init__(self, pid: int) -> None:
        super().__init__(f"another index of this repo is running (pid {pid})")
        self.pid = pid


def _lock_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _LOCK_FILE


def _alive(pid: int) -> bool:
    from codegraph.state.pidfile import process_alive

    return process_alive(pid)


def holder(repo_root: str | Path) -> dict | None:
    """The index running on this repo as {"pid", "since"} (epoch seconds), or
    None when none is. A lock file left by a process that died counts as none."""
    path = _lock_path(repo_root)
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
        since = path.stat().st_mtime
    except (OSError, ValueError, IndexError):
        return None
    return {"pid": pid, "since": since} if pid > 0 and _alive(pid) else None


def _acquire_file(root: Path) -> None:
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            running = holder(root)
            if running is not None and running["pid"] != os.getpid():
                raise IndexBusy(running["pid"]) from None
            # Left by a process that died mid-index (or an earlier run of this
            # very pid that never cleaned up): not a live index, so take over.
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n")
        return
    running = holder(root)
    raise IndexBusy(running["pid"] if running else -1)


def _release_file(root: Path) -> None:
    path = _lock_path(root)
    try:
        mine = int(path.read_text(encoding="utf-8").split()[0]) == os.getpid()
    except (OSError, ValueError, IndexError):
        return
    if mine:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


@contextlib.contextmanager
def index_lock(repo_root: str | Path) -> Generator[None]:
    """Hold this repo's index lock for the duration of the block, or raise
    IndexBusy if another process or thread is indexing it."""
    key = str(Path(repo_root).resolve())
    with _GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    if not thread_lock.acquire(blocking=False):
        raise IndexBusy(os.getpid())
    try:
        with _GUARD:
            depth = _DEPTH.get(key, 0)
            _DEPTH[key] = depth + 1
        try:
            if depth == 0:
                _acquire_file(Path(key))
            try:
                yield
            finally:
                if depth == 0:
                    _release_file(Path(key))
        finally:
            with _GUARD:
                _DEPTH[key] -= 1
    finally:
        thread_lock.release()
