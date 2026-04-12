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


# Module-level handles so MCP tools can hot-extend the watcher to new dirs
_active_observer: Observer | None = None
_active_handler: _CodeGraphHandler | None = None


class _AuxRescanHandler(FileSystemEventHandler):
    """
    Debounced handler that triggers a folder-level rescan callback on
    any .md change. Used for the memory dir and the plans dir — we
    re-scan the entire (small) folder instead of tracking per-file state.
    """

    def __init__(self, repo_root: Path, label: str, scan_fn) -> None:
        super().__init__()
        self._root = repo_root
        self._label = label
        self._scan_fn = scan_fn
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self, path: str) -> None:
        if not path.endswith(".md"):
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE * 3, self._rescan)
            self._timer.start()

    def _rescan(self) -> None:
        try:
            stats = self._scan_fn(self._root)
            print(
                f"[codegraph] {self._label} rescan: "
                f"indexed={stats.get('indexed', 0)} removed={stats.get('removed', 0)}",
                flush=True,
            )
        except Exception as exc:
            print(f"[codegraph] {self._label} rescan error: {exc}", flush=True)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.dest_path)


def start_watcher(repo_root: str | Path) -> Observer:
    """
    Start a background file watcher for `repo_root`.
    Returns the Observer so the caller can stop it with observer.stop().
    Also watches extra_dirs, the Claude Code memory dir, and the plans dir.
    """
    global _active_observer, _active_handler

    root = Path(repo_root).resolve()
    handler = _CodeGraphHandler(root)
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)

    # Also watch extra_dirs from config
    extra_paths: list[Path] = []
    try:
        import tomllib

        cfg = root / ".codegraph" / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            for rel in data.get("codegraph", {}).get("extra_dirs", []):
                p = (root / rel).resolve()
                if p.exists() and p.is_dir():
                    observer.schedule(handler, str(p), recursive=True)
                    extra_paths.append(p)
    except Exception:
        pass

    # Memory + plans dirs (auto-discovered; env / config-overridable)
    from codegraph.config import memory_dir, plans_dir
    from codegraph.memory_index import scan_memory_dir
    from codegraph.plan_index import scan_plan_dir

    aux_targets: list[tuple[Path, str, object]] = []
    try:
        mdir = memory_dir(root)
        if mdir.exists() and mdir.is_dir():
            aux_targets.append((mdir, "memory", scan_memory_dir))
    except Exception:
        pass
    try:
        pdir = plans_dir(root)
        if pdir.exists() and pdir.is_dir():
            aux_targets.append((pdir, "plans", scan_plan_dir))
    except Exception:
        pass

    for path, label, scan_fn in aux_targets:
        aux_handler = _AuxRescanHandler(root, label, scan_fn)
        observer.schedule(aux_handler, str(path), recursive=False)

    observer.start()
    print(f"[codegraph] watching {root}", flush=True)
    for p in extra_paths:
        print(f"[codegraph] watching {p} (extra_dir)", flush=True)
    for path, label, _ in aux_targets:
        print(f"[codegraph] watching {path} ({label})", flush=True)

    _active_observer = observer
    _active_handler = handler
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
