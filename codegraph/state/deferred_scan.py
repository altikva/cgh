# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deferred scanner queue. Heavy scanners (NER, model calls)
#              never run in the indexing hot path: files are queued here
#              and a single low-priority daemon thread works through
#              them, skipping any (file, scanner, blob SHA) combination
#              already recorded in the finding store. Pending items die
#              with the process; that is by design, the owner re-covers
#              them on the next change and short-lived CLI runs stay
#              short-lived.

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

_QUEUE: queue.Queue[tuple[str, str, str]] = queue.Queue()
_WORKER_STARTED = threading.Event()
_LOCK = threading.Lock()


def enqueue(repo_root: str | Path, path: str | Path, blob_sha: str) -> None:
    """Queue a file for every registered deferred scanner. Cheap and
    non-blocking; starts the worker thread on first use."""
    _QUEUE.put((str(repo_root), str(path), blob_sha))
    _ensure_worker()


def _ensure_worker() -> None:
    if _WORKER_STARTED.is_set():
        return
    with _LOCK:
        if _WORKER_STARTED.is_set():
            return
        t = threading.Thread(target=_worker, name="cgh-deferred-scan", daemon=True)
        t.start()
        _WORKER_STARTED.set()


def _worker() -> None:
    while True:
        repo_root, path, blob_sha = _QUEUE.get()
        try:
            _process(repo_root, path, blob_sha)
        except Exception as exc:
            logging.getLogger(__name__).error("deferred scan error: %s: %s", path, exc)
        finally:
            _QUEUE.task_done()


def _process(repo_root: str, path: str, blob_sha: str) -> None:
    from codegraph.plugins import scanners as _plugin_scanners
    from codegraph.state.findings import already_scanned, record_findings

    deferred = [(n, s) for n, s in _plugin_scanners() if getattr(s, "deferred", False)]
    if not deferred:
        return

    p = Path(path)
    if not p.exists():
        return
    text: str | None = None

    for _plugin_name, scanner in deferred:
        from codegraph.indexer import _bind_scanner_root

        _bind_scanner_root(scanner, repo_root)
        if already_scanned(repo_root, path, scanner.name, blob_sha):
            continue
        if text is None:
            try:
                # Binary files (docx, xlsx, images) decode with embedded
                # nulls; strip them so scanners and their backends never
                # receive text no OS API will accept.
                text = p.read_text(encoding="utf-8", errors="replace").replace(
                    "\x00", ""
                )
            except OSError:
                return
        found = scanner.scan(p, text, None) or []
        record_findings(repo_root, path, scanner.name, found, blob_sha=blob_sha)
        _feed_fts(repo_root, path, scanner.name, found)


def _feed_fts(repo_root: str, path: str, scanner: str, found: list) -> None:
    try:
        from codegraph.core.fts import commit as _commit
        from codegraph.core.fts import get_fts_conn
        from codegraph.indexer import _fts_ingest_findings

        fts_conn = get_fts_conn(repo_root)
        _fts_ingest_findings(fts_conn, path, scanner, found)
        _commit(fts_conn)
    except Exception:
        pass


def drain_for_tests(timeout: float = 5.0) -> None:
    """Block until the queue is empty. Test helper."""
    import time

    deadline = time.time() + timeout
    while not _QUEUE.empty() and time.time() < deadline:
        time.sleep(0.05)
    _QUEUE.join()
