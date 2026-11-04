# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Filtered, lossless transfer of a knowledge store between checkouts.
#              export_records reads a store read-only and yields one dict per
#              knowledge row (every column, provenance included) and per outcome
#              row, filtered by time, kind and tag glob. import_records writes
#              them into another store with new ids, remapping supersede links
#              and outcome targets, and skipping exact (title, body, kind)
#              duplicates. Session digests and checkpoints live in the same
#              knowledge table, so the same filters apply to them.

from __future__ import annotations

import fnmatch
import re
import sqlite3
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from codegraph.state.call_log import _LOG_LOCK, _SESSION_STATE_TAGS, _get_conn

RECORD_KNOWLEDGE = "knowledge"
RECORD_OUTCOME = "outcome"

# Provenance columns exposed to --exclude-tag as "<prefix>:<value>" pseudo-tags,
# so an entry promoted from a ticket's PR or branch can be excluded with the
# same glob syntax as a real tag (pr:*#2488, branch:feature/x).
_PROVENANCE_TAGS = (
    ("pr", "source_pr"),
    ("branch", "source_branch"),
    ("commit", "source_commit"),
    ("session", "source_session"),
)


class KnowledgeTransferError(Exception):
    """A bad time bound, an unreadable store or a malformed import line."""


def split_tags(tags: str | None) -> list[str]:
    """Individual tags from a stored tag string: comma or space separated."""
    return [t for t in re.split(r"[,\s]+", tags or "") if t]


def entry_tags(row: dict) -> list[str]:
    """Real tags plus the provenance pseudo-tags the filters match against."""
    out = split_tags(row.get("tags"))
    for prefix, col in _PROVENANCE_TAGS:
        value = (row.get(col) or "").strip()
        if value:
            out.append(f"{prefix}:{value}")
    return out


def is_session_state(row: dict) -> bool:
    """A session digest or checkpoint, as resume tells them apart: a
    session-state tag, or a legacy untagged digest (a note carrying a
    session id)."""
    if set(split_tags(row.get("tags"))) & _SESSION_STATE_TAGS:
        return True
    return row.get("kind") == "note" and bool(row.get("session_id"))


def parse_time(value: str) -> float:
    """An epoch number or an ISO 8601 date/datetime (UTC when no zone)."""
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeTransferError(f"not an epoch or ISO date: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def commit_time(root: str | Path, rev: str) -> float:
    """Committer date of ``rev`` in the git repository at ``root``."""
    rev = rev.strip()
    if not rev or rev.startswith("-"):
        raise KnowledgeTransferError(f"not a commit: {rev!r}")
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct",
             "--end-of-options", f"{rev}^{{commit}}", "--"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise KnowledgeTransferError(f"git failed: {exc}") from exc
    if out.returncode != 0 or not out.stdout.strip():
        detail = out.stderr.strip() or "unknown revision"
        raise KnowledgeTransferError(f"cannot resolve {rev!r} in {root}: {detail}")
    return float(out.stdout.strip())


def _ro_connect(repo_root: str | Path) -> sqlite3.Connection:
    """A read-only connection: export must not write, nor wait on, a store a
    running owner holds."""
    path = Path(repo_root) / ".codegraph" / "call_log.db"
    if not path.is_file():
        raise KnowledgeTransferError(f"no knowledge store at {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    # table is one of two constants, never caller input.
    if table not in ("knowledge", "knowledge_outcome"):
        raise ValueError(table)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def export_records(
    repo_root: str | Path,
    *,
    before: float | None = None,
    exclude_tags: Iterable[str] = (),
    kinds: Iterable[str] | None = None,
    include_digests: bool = True,
) -> list[dict]:
    """Knowledge rows, then outcome rows, each tagged with a ``record`` field.

    A knowledge row is kept when its ``ts`` (the time it entered this store,
    which for a promoted entry is the promotion time) is strictly before
    ``before``, its kind is in ``kinds``, no tag or provenance pseudo-tag
    matches an ``exclude_tags`` glob, and, unless ``include_digests``, it is
    not a digest or checkpoint. A supersede link to a row that was filtered
    out is cleared: at that time the older row was still live. An outcome is
    kept when its ts passes and the entry it rates was kept.
    """
    globs = [g for g in exclude_tags if g]
    wanted = {k for k in kinds} if kinds else None
    conn = _ro_connect(repo_root)
    try:
        knowledge = _rows(conn, "knowledge")
        outcomes = _rows(conn, "knowledge_outcome")
    finally:
        conn.close()

    kept: list[dict] = []
    for row in knowledge:
        if before is not None and float(row.get("ts") or 0.0) >= before:
            continue
        if wanted is not None and row.get("kind") not in wanted:
            continue
        if not include_digests and is_session_state(row):
            continue
        tags = entry_tags(row)
        if any(fnmatch.fnmatchcase(t, g) for t in tags for g in globs):
            continue
        kept.append(row)
    ids = {r["id"] for r in kept}
    out: list[dict] = []
    for row in kept:
        if row.get("superseded_by") is not None and row["superseded_by"] not in ids:
            row = {**row, "superseded_by": None}
        out.append({"record": RECORD_KNOWLEDGE, **row})
    for row in outcomes:
        if before is not None and float(row.get("ts") or 0.0) >= before:
            continue
        if row.get("entry_id") is not None and row["entry_id"] not in ids:
            continue
        if row.get("corrected_by") is not None and row["corrected_by"] not in ids:
            row = {**row, "corrected_by": None}
        out.append({"record": RECORD_OUTCOME, **row})
    return out


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def import_records(repo_root: str | Path, records: Iterable[dict]) -> dict:
    """Insert exported records into ``repo_root``'s store, in one transaction.

    Knowledge rows get new ids and keep every other column (ts and provenance
    included). A row whose (title, body, kind) already exists in the target,
    or earlier in the same input, is skipped and its id maps to the existing
    row. Supersede links and outcome targets are remapped to the new ids; an
    outcome whose entry was not imported is dropped, as is an outcome already
    present. The FTS index follows through the table's insert and update
    triggers. Returns counts.
    """
    knowledge: list[dict] = []
    outcomes: list[dict] = []
    for i, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            raise KnowledgeTransferError(f"record {i}: not a JSON object")
        kind = rec.get("record", RECORD_KNOWLEDGE)
        if kind == RECORD_KNOWLEDGE:
            if rec.get("ts") is None:
                raise KnowledgeTransferError(f"record {i}: knowledge row without ts")
            knowledge.append(rec)
        elif kind == RECORD_OUTCOME:
            outcomes.append(rec)
        else:
            raise KnowledgeTransferError(f"record {i}: unknown record type {kind!r}")

    counts = {
        "imported": 0,
        "skipped_duplicate": 0,
        "outcomes_imported": 0,
        "outcomes_skipped": 0,
    }
    with _LOG_LOCK:
        conn = _get_conn(repo_root)
        # The target's own columns are the allow-list for every interpolated
        # column name below; a key in the input that is not one is ignored.
        k_cols = [c for c in _columns(conn, "knowledge") if c != "id"]
        o_cols = [c for c in _columns(conn, "knowledge_outcome") if c != "id"]
        existing = {
            (kind, title, body): rid
            for rid, kind, title, body in conn.execute(
                "SELECT id, kind, title, body FROM knowledge ORDER BY id DESC"
            )
        }
        id_map: dict[int, int] = {}
        links: list[tuple[int, int]] = []
        try:
            for rec in knowledge:
                key = (rec.get("kind"), rec.get("title"), rec.get("body"))
                old_id = rec.get("id")
                if key in existing:
                    counts["skipped_duplicate"] += 1
                    if old_id is not None:
                        id_map[int(old_id)] = existing[key]
                    continue
                cols = [c for c in k_cols if c in rec and c != "superseded_by"]
                cur = conn.execute(
                    f"INSERT INTO knowledge({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})",
                    [rec[c] for c in cols],
                )
                new_id = int(cur.lastrowid or 0)
                existing[key] = new_id
                counts["imported"] += 1
                if old_id is not None:
                    id_map[int(old_id)] = new_id
                if rec.get("superseded_by") is not None:
                    links.append((new_id, int(rec["superseded_by"])))
            for new_id, old_target in links:
                target = id_map.get(old_target)
                if target is not None and target != new_id:
                    conn.execute(
                        "UPDATE knowledge SET superseded_by = ? WHERE id = ?",
                        (target, new_id),
                    )
            seen = set(
                conn.execute(
                    "SELECT entry_id, verdict, note, file_refs, ts "
                    "FROM knowledge_outcome"
                ).fetchall()
            )
            for rec in outcomes:
                row = {c: rec[c] for c in o_cols if c in rec}
                for ref in ("entry_id", "corrected_by"):
                    if row.get(ref) is not None:
                        row[ref] = id_map.get(int(row[ref]))
                if rec.get("entry_id") is not None and row.get("entry_id") is None:
                    counts["outcomes_skipped"] += 1
                    continue
                sig = (
                    row.get("entry_id"),
                    row.get("verdict"),
                    row.get("note", ""),
                    row.get("file_refs", ""),
                    row.get("ts"),
                )
                if sig in seen:
                    counts["outcomes_skipped"] += 1
                    continue
                cols = list(row)
                conn.execute(
                    f"INSERT INTO knowledge_outcome({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})",
                    [row[c] for c in cols],
                )
                seen.add(sig)
                counts["outcomes_imported"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return counts
