# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: On-demand indexing of one file the graph does not hold yet (a
#              new file the watcher has not reached, one changed since its
#              last index) so a lookup answers from the graph instead of a raw
#              read. Two sides:
#              - inside the owner (the one process holding the graph write
#                connection): ensure_indexed indexes a named file, catch_up
#                the working-tree files newer than their index, both bounded
#                by BUDGET_S and never waiting on a running full index;
#              - in a hook process, which must never open the graph for
#                writing: classify decides whether a file may be indexed at
#                all, transient_rows parses it in memory for an outline and
#                writes nothing (files outside the repo, skipped paths, or
#                no live owner).
#              A gitignored file is never indexed nor parsed here.

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codegraph.core.utils import quiet_subprocess_kwargs

# How long a lookup may spend making a file indexable before it gives up and
# answers as it would have without it.
BUDGET_S = 0.3
# Binary documents parse slowly (pdf text extraction, zip-based office files)
# and have their own Read path: never on demand. Plain text and logs parse
# into one section spanning the file, a worse plan than the hook's anchors,
# and a log grows between two reads.
_NO_ON_DEMAND = frozenset(
    {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".txt", ".text", ".log"}
)
# A file this large is not parsed in a hook: the parse alone would pass the
# budget.
TRANSIENT_MAX = 1024 * 1024
# How long the owner waits for the index write lock before giving up: a
# running full index holds it for minutes, and the file is then reached by it.
_LOCK_WAIT_S = 0.05
# catch_up: at most this many candidate files per call, and one git listing
# per repo per interval, so a run of misses does not spawn git each time.
_CATCH_UP_MAX = 40
_CATCH_UP_INTERVAL_S = 1.0
_last_catch_up: dict[str, float] = {}

Decision = Literal["index", "transient", "ignored", "unsupported"]
# What ensure_indexed did: "fresh" (the graph already held this version),
# "indexed" (written just now), "ignored" (gitignored, left alone),
# "transient" (outside the repo or under a path the index skips),
# "unsupported", "busy" (a full index holds the lock) or "failed" (the parse
# or the write failed).
Status = Literal[
    "fresh", "indexed", "ignored", "transient", "unsupported", "busy", "failed"
]

# A symbol row as the hook formats it: kind, name, start line, end line.
Row = tuple[str, str, int, int]


def budget_s() -> float:
    """The on-demand budget in seconds; CGH_ON_DEMAND_BUDGET_MS overrides it,
    and 0 turns on-demand indexing off."""
    raw = os.environ.get("CGH_ON_DEMAND_BUDGET_MS", "").strip()
    if not raw:
        return BUDGET_S
    try:
        return max(0.0, float(raw) / 1000)
    except ValueError:
        return BUDGET_S


def eligible_type(target: Path) -> bool:
    """True when a cgh parser reads this kind of file and on-demand applies."""
    if target.suffix.lower() in _NO_ON_DEMAND:
        return False
    from codegraph.parsers import is_supported

    return is_supported(target)


def writable_root(root: Path) -> bool:
    """True when on-demand indexing may write to the index of ``root``: a
    git checkout that is not the home directory. A cgh index of the home
    directory spans every checkout and scratch file below it, so a file
    there is only ever parsed in memory."""
    try:
        resolved = root.resolve()
        if resolved == Path.home().resolve():
            return False
    except (OSError, RuntimeError):
        return False
    return (resolved / ".git").exists()


def relative(target: Path, root: Path) -> Path | None:
    """``target`` relative to ``root``, or None when it lies outside it."""
    try:
        return target.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None


def canonical(target: Path, root: Path) -> Path:
    """The path the index stores for a repo file: the root joined with the
    relative path, the way a full index names it (a resolved path would
    differ from it under a symlinked root such as /tmp on macOS)."""
    rel = relative(target, root)
    return root / rel if rel is not None else target


def git_ignored(root: Path, target: Path, timeout: float) -> bool:
    """True when git ignores ``target``. Outside a git repo, or when git
    fails or is slow, the file counts as not ignored, as for the watcher."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(target)],
            cwd=str(root),
            capture_output=True,
            timeout=max(timeout, 0.01),
            **quiet_subprocess_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _skipped(target: Path, rel: Path, root: Path) -> bool:
    """True when a full index leaves this repo file out for a reason other
    than git: an ignored directory, .cghignore, a federated child (which owns
    its own index), an ignore pattern or the size cap of the config."""
    import fnmatch

    from codegraph.analysis.federation import child_paths_to_skip, is_under_any
    from codegraph.core.config import load_config
    from codegraph.indexer import _is_cghignored, skipped_path_parts

    if skipped_path_parts(rel.parts) or _is_cghignored(root / rel, root):
        return True
    subrepos = child_paths_to_skip(root)
    if subrepos and is_under_any(root / rel, subrepos):
        return True
    try:
        cfg = load_config(root)
        if any(fnmatch.fnmatch(target.name, p) for p in cfg.ignore_patterns):
            return True
        return target.stat().st_size > cfg.max_file_size_kb * 1024
    except Exception:
        return False


def classify(target: Path, root: Path, timeout: float = BUDGET_S) -> Decision:
    """What may be done with ``target`` for the repo at ``root``.

    "index": a repo file a full index would include; "transient": outside
    the repo, or a repo file the index skips, so it is parsed in memory and
    never written; "ignored": gitignored, left alone; "unsupported": no
    parser for it, or not a file."""
    if not target.is_file() or not eligible_type(target):
        return "unsupported"
    rel = relative(target, root)
    if rel is None:
        return "transient"
    if git_ignored(root, rel, timeout):
        return "ignored"
    return "transient" if _skipped(target, rel, root) else "index"


def fts_behind(root: Path, target: Path) -> bool:
    """True when ``target`` changed after the last write to the repo's FTS
    index, so its rows there may be stale. Cheap enough for a hook: two
    stats. Every index of a file ends with an FTS write, so a file older
    than that write was indexed in its current form, or skipped."""
    db = root / ".codegraph" / "fts.db"
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return False
    last = 0.0
    for path in (db, db.with_name(db.name + "-wal")):
        with contextlib.suppress(OSError):
            last = max(last, path.stat().st_mtime)
    return mtime > last


def transient_rows(target: Path, timeout: float) -> list[Row] | None:
    """Parse ``target`` in memory and return its symbols, nothing written.

    None when it is too large, fails to parse, or the parse does not finish
    within ``timeout`` (it then runs on in a daemon thread and its result is
    dropped)."""
    try:
        if target.stat().st_size > TRANSIENT_MAX:
            return None
    except OSError:
        return None
    box: dict[str, object] = {}

    def run() -> None:
        try:
            from codegraph.parsers import get_parser_for_path

            parser = get_parser_for_path(target)
            if parser is not None:
                box["idx"] = parser.parse(target)
        except BaseException as exc:  # a parse failure only costs the outline
            box["error"] = exc

    worker = threading.Thread(target=run, name="cgh-transient-parse", daemon=True)
    worker.start()
    worker.join(timeout)
    idx = box.get("idx")
    if worker.is_alive() or idx is None:
        return None
    return rows_of(idx)


def rows_of(idx) -> list[Row]:
    """The symbol rows of a parsed FileIndex, in the shape and order the FTS
    holds them (by start line; sections as md_section)."""
    rows: list[Row] = []
    for fn in idx.functions:
        rows.append((fn.kind, fn.name, fn.start_line, fn.end_line))
    for cls in idx.classes:
        rows.append((cls.kind, cls.name, cls.start_line, cls.end_line))
    for res in idx.resources:
        rows.append(
            (f"tf_{res.kind}", res.address or res.name, res.start_line, res.end_line)
        )
    for sec in idx.sections:
        rows.append(("md_section", sec.title, sec.start_line, sec.end_line))
    rows.sort(key=lambda r: (r[2], r[3]))
    return rows


# --- inside the owner ---------------------------------------------------------


@contextlib.contextmanager
def _try_write_lock(root: Path, wait: float) -> Generator[bool]:
    """The index write lock of this process when it frees up within ``wait``
    seconds, else False: a full index holds it for its whole run."""
    from codegraph.state import index_lock

    key = str(Path(root).resolve())
    with index_lock._GUARD:
        lock = index_lock._THREAD_LOCKS.setdefault(key, threading.RLock())
    got = lock.acquire(timeout=wait)
    try:
        yield got
    finally:
        if got:
            lock.release()


def graph_stale(conn, path: Path) -> bool:
    """True when the graph has no File node for ``path`` or one stamped with
    another mtime than the file on disk."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    try:
        stored = conn.query_node_field("File", "path", str(path), "mtime")
    except Exception:
        return True
    return stored is None or abs(float(stored) - mtime) >= 0.01


def _index_now(root: Path, path: Path) -> Status:
    from codegraph.indexer import index_file

    with _try_write_lock(root, _LOCK_WAIT_S) as got:
        if not got:
            return "busy"
        try:
            return "indexed" if index_file(path, root) else "failed"
        except Exception:
            return "failed"


def ensure_indexed(root: Path, target: Path) -> Status:
    """Inside the owner: make sure the graph holds the current version of
    ``target``, indexing it now when it is missing or stale and a full index
    would include it. Never indexes a file outside the repo or a gitignored
    one. Uses the owner's write connection; a hook must not call this."""
    if budget_s() <= 0:
        return "fresh"
    if not target.is_file() or not eligible_type(target):
        return "unsupported"
    rel = relative(target, root)
    if rel is None:
        return "transient"
    path = root / rel
    from codegraph.core.db import get_connection

    try:
        if not graph_stale(get_connection(root), path):
            return "fresh"
    except Exception:
        return "failed"
    if not writable_root(root):
        return "transient"
    decision = classify(path, root, budget_s())
    if decision != "index":
        return decision
    return _index_now(root, path)


def _changed_files(root: Path, timeout: float) -> list[str]:
    """The working-tree files git reports modified or untracked (and not
    ignored), relative to ``root``."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--modified", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(timeout, 0.01),
            **quiet_subprocess_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return list(dict.fromkeys(p for p in result.stdout.split("\0") if p))


def catch_up(root: Path | None) -> list[str]:
    """Inside the owner, for a name lookup that found nothing: index the
    working-tree files newer than their index (a file just written, one the
    watcher has not reached), within the budget. Returns the repo-relative
    paths indexed, so the caller retries once when there are any."""
    budget = budget_s()
    if root is None or budget <= 0 or not writable_root(root):
        return []
    key = str(root)
    now = time.monotonic()
    if now - _last_catch_up.get(key, -_CATCH_UP_INTERVAL_S) < _CATCH_UP_INTERVAL_S:
        return []
    _last_catch_up[key] = now
    deadline = now + budget
    from codegraph.core.db import get_connection

    try:
        conn = get_connection(root)
    except Exception:
        return []
    indexed: list[str] = []
    candidates = _changed_files(root, budget)
    for rel in candidates[:_CATCH_UP_MAX]:
        if time.monotonic() >= deadline:
            break
        path = root / rel
        if not path.is_file() or not eligible_type(path):
            continue
        if not graph_stale(conn, path) or _skipped(path, Path(rel), root):
            continue
        status = _index_now(root, path)
        if status == "busy":
            break
        if status == "indexed":
            indexed.append(rel)
    return indexed


# --- in a hook process --------------------------------------------------------


@dataclass
class HookOutcome:
    """What the on-demand step got a hook. ``indexed``: the owner wrote the
    file just now, its rows are in the FTS. Otherwise ``rows`` holds the
    symbols of an in-memory parse, nothing written."""

    indexed: bool = False
    rows: list[Row] | None = None


def _ask_owner(root: Path, rel: Path, timeout: float) -> str | None:
    """Have the live owner index ``rel`` when it needs it, through
    file_summary (which indexes on demand). Returns the owner's on_demand
    status ("fresh" when it said none), "absent" without a live owner, or
    None when the owner did not answer usefully in time."""
    from codegraph.cli.owner_client import call_owner_tool, live_owner_port

    port = live_owner_port(str(root))
    if not port:
        return "absent"
    reply = call_owner_tool(
        str(root),
        "file_summary",
        {"file_path": rel.as_posix()},
        timeout=max(timeout, 0.01),
        port=port,
    )
    if not reply.ok or not isinstance(reply.data, dict):
        return None
    status = reply.data.get("on_demand")
    return status if isinstance(status, str) else "fresh"


def for_hook(
    root: Path, target: Path, indexed_rows: int, *, parse: bool = True
) -> HookOutcome | None:
    """The on-demand step of a Read or Grep hook for ``target``, or None when
    there is nothing to add and the hook answers as before.

    A repo file the FTS lacks, or holds in an older version, is indexed by
    the live owner when one answers within the budget; with no owner it is
    parsed in memory (``parse``), since a hook never opens the graph for
    writing. A file outside the repo, or under a path the index skips, is
    only parsed in memory. A gitignored file is left alone."""
    budget = budget_s()
    if budget <= 0:
        return None
    if indexed_rows and not fts_behind(root, target):
        return None
    if not target.is_file() or not eligible_type(target):
        return None
    deadline = time.monotonic() + budget
    rel = relative(target, root)
    decision: str | None = None if rel is not None else "transient"
    if rel is not None and not writable_root(root):
        # The home directory, or no git checkout: never ask for a write.
        decision = "transient"
    elif rel is not None:
        status = _ask_owner(root, rel, budget)
        if status == "indexed":
            return HookOutcome(indexed=True)
        if status in ("fresh", "ignored", "unsupported"):
            return None
        if status is None:
            # The owner holds the graph but did not answer in time: the
            # budget is spent, answer as before.
            return None
        if status == "transient":
            decision = "transient"
    if not parse:
        return None
    if decision is None:
        decision = classify(target, root, max(deadline - time.monotonic(), 0.01))
    if decision not in ("index", "transient"):
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    rows = transient_rows(target, left)
    return HookOutcome(rows=rows) if rows is not None else None


def note(indexed: list[str] | Status) -> str:
    """The sentence an MCP answer carries after an on-demand index."""
    if isinstance(indexed, list):
        files = ", ".join(indexed[:5]) + (" ..." if len(indexed) > 5 else "")
        return f"Not in the index until now: indexed just now ({files}), then looked up again."
    return "This file was not in the index (or was stale): indexed just now."


def answer_fields(status: Status) -> dict:
    """The fields an MCP answer about one file adds after ensure_indexed."""
    if status != "indexed":
        return {}
    return {"indexed_now": True, "note": note(status)}


__all__ = [
    "BUDGET_S",
    "TRANSIENT_MAX",
    "HookOutcome",
    "answer_fields",
    "budget_s",
    "canonical",
    "catch_up",
    "classify",
    "eligible_type",
    "ensure_indexed",
    "for_hook",
    "fts_behind",
    "git_ignored",
    "graph_stale",
    "note",
    "relative",
    "rows_of",
    "transient_rows",
    "writable_root",
]
