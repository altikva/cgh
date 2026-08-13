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

# Deferred scan errors are collapsed by message, not logged per file: a
# misconfigured backend (say a summarize model that 404s) would otherwise
# print the same line once per file. Each distinct message is counted with
# one sample path and flushed as a single summary line when the queue
# drains, so identical errors read as one line and different errors each
# get their own.
_ERR_LOCK = threading.Lock()
_ERR_COUNTS: dict[str, int] = {}
_ERR_SAMPLE: dict[str, str] = {}


def _record_error(path: str, exc: Exception) -> None:
    key = str(exc)
    with _ERR_LOCK:
        _ERR_COUNTS[key] = _ERR_COUNTS.get(key, 0) + 1
        _ERR_SAMPLE.setdefault(key, str(path))


def _flush_errors() -> None:
    """Emit one line per distinct error accumulated since the last flush,
    then reset. Called when the queue drains."""
    with _ERR_LOCK:
        if not _ERR_COUNTS:
            return
        items = list(_ERR_COUNTS.items())
        samples = dict(_ERR_SAMPLE)
        _ERR_COUNTS.clear()
        _ERR_SAMPLE.clear()
    log = logging.getLogger(__name__)
    for msg, count in items:
        if count == 1:
            log.error("deferred scan error: %s: %s", samples.get(msg, ""), msg)
        else:
            log.error(
                "deferred scan error x%d (e.g. %s): %s", count, samples.get(msg, ""), msg
            )


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


_FLUSH_QUIET_S = 1.0  # flush the error summary after this much quiet


def _worker() -> None:
    while True:
        try:
            # Block for the item, but wake up after a quiet period so a
            # finished burst (an index run, a watcher batch) flushes its
            # error summary once. A busy queue never times out.
            repo_root, path, blob_sha = _QUEUE.get(timeout=_FLUSH_QUIET_S)
        except queue.Empty:
            _flush_errors()
            continue
        try:
            _process(repo_root, path, blob_sha)
        except Exception as exc:
            # Collapse repeats: count now, one summary line per distinct
            # message on the next quiet period, so a bad backend does not
            # print N lines.
            _record_error(path, exc)
        finally:
            _QUEUE.task_done()


def _reparse(p: Path):
    """Best-effort re-parse to recover a parser's extracted text
    (idx.scan_text) for a binary/compound doc. None if no parser matches
    or parsing fails; the caller then falls back to raw bytes."""
    try:
        from codegraph.parsers import get_parser_for_path

        parser = get_parser_for_path(p)
        if parser is None:
            return None
        return parser.parse(p)
    except Exception:
        return None


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
    idx = None

    for _plugin_name, scanner in deferred:
        from codegraph.indexer import _bind_scanner_root

        _bind_scanner_root(scanner, repo_root)
        if already_scanned(repo_root, path, scanner.name, blob_sha):
            continue
        if text is None:
            # Re-parse so a binary/compound format (pdf, xlsx, docx) is
            # scanned on its extracted text (idx.scan_text), not its raw
            # bytes: reading a pdf or a zip-based xlsx as text is binary
            # noise that both fakes PII hits and hides the real content.
            idx = _reparse(p)
            if idx is not None and idx.scan_text:
                text = idx.scan_text.replace("\x00", "")
            else:
                try:
                    # Embedded nulls decode into binary-ish files; strip
                    # them so scanners and their backends never receive
                    # text no OS API will accept.
                    text = p.read_text(encoding="utf-8", errors="replace").replace(
                        "\x00", ""
                    )
                except OSError:
                    return
        found = scanner.scan(p, text, idx) or []
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
    _flush_errors()  # deterministic summary for the caller / tests
