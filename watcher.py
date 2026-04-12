# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Watchdog-based file watcher.  Triggers incremental re-indexing
#              when source files are created or modified.  Debounced to 300 ms
#              to avoid double-firing on editor write patterns.
#
#              Ignore chain: _IGNORE_DIRS -> .cghignore -> git check-ignore (batched)

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import _IGNORE_DIRS, _is_cghignored, index_file
from .parsers import is_supported

# Debounce window in seconds
_DEBOUNCE = 0.3

# Cache git-ignored status to avoid subprocess per file
_GIT_IGNORE_CACHE: dict[str, bool] = {}
_GIT_IGNORE_CACHE_TTL = 60  # seconds
_GIT_IGNORE_CACHE_TS: float = 0


class _CodeGraphHandler(FileSystemEventHandler):
    def __init__(self, repo_root: Path) -> None:
        super().__init__()
        self._root = repo_root
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _should_ignore(self, path: str) -> bool:
        p = Path(path)

        # 1. Unsupported extension
        if not is_supported(p):
            return True

        # 2. Hardcoded ignored dirs
        for part in p.parts:
            if part in _IGNORE_DIRS or part.startswith("."):
                return True

        # 3. .cghignore (fast, in-memory)
        if _is_cghignored(p, self._root):
            return True

        # 4. git check-ignore (cached)
        if self._is_gitignored(path):
            return True

        return False

    def _is_gitignored(self, path: str) -> bool:
        """Check git ignore status with caching to avoid subprocess spam."""
        global _GIT_IGNORE_CACHE, _GIT_IGNORE_CACHE_TS

        # Expire cache periodically
        now = time.time()
        if now - _GIT_IGNORE_CACHE_TS > _GIT_IGNORE_CACHE_TTL:
            _GIT_IGNORE_CACHE.clear()
            _GIT_IGNORE_CACHE_TS = now

        if path in _GIT_IGNORE_CACHE:
            return _GIT_IGNORE_CACHE[path]

        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", path],
                capture_output=True,
                cwd=str(self._root),
                timeout=2,
            )
            ignored = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            ignored = False

        _GIT_IGNORE_CACHE[path] = ignored
        return ignored

    def _schedule(self, path: str) -> None:
        if self._should_ignore(path):
            return
        with self._lock:
            existing = self._timers.pop(path, None)
            if existing:
                existing.cancel()
            t = threading.Timer(_DEBOUNCE, self._reindex, args=(path,))
            self._timers[path] = t
            t.start()

    def _reindex(self, path: str) -> None:
        from codegraph.activity import log as _activity_log

        with self._lock:
            self._timers.pop(path, None)
        try:
            ok = index_file(path, self._root)
            if ok:
                rel = Path(path).relative_to(self._root)
                print(f"[codegraph] + {rel}", flush=True)
                _activity_log(self._root, "reindex", str(rel))
        except Exception as exc:
            print(f"[codegraph] error {path}: {exc}", flush=True)
            _activity_log(self._root, "error", f"{path}: {exc}")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.dest_path)


def start_watcher(repo_root: str | Path) -> Observer:
    """
    Start a background file watcher for `repo_root`.
    Returns the Observer so the caller can stop it with observer.stop().
    """
    root = Path(repo_root).resolve()
    handler = _CodeGraphHandler(root)
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    print(f"[codegraph] watching {root}", flush=True)
    return observer


def watch_forever(repo_root: str | Path) -> None:
    """Block forever, watching for changes.  Ctrl-C to stop."""
    observer = start_watcher(repo_root)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
