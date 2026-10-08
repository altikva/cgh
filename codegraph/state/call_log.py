# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Call logger for codegraph MCP tools.
#              Logs every tool invocation with args, result size, latency.
#              Backed by SQLite for persistence and fast aggregation.

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_DB_DIR = ".codegraph"
_LOG_FILE = "call_log.db"

# Who triggered a tool call. "agent": an MCP client through the stdio proxy.
# "cli": a `cgh` command asking the owner over HTTP. "hook": the same, from a
# hook entry point (agent lifecycle hooks, git hooks). "internal": a tool
# function called in-process, outside any HTTP request.
ORIGINS = ("agent", "hook", "cli", "internal")
# HTTP header the proxy and the CLI client send so the owner can tell them apart.
ORIGIN_HEADER = "X-Cgh-Origin"
# Environment variable a hook entry point sets so the owner calls it makes
# are tagged "hook" instead of "cli".
ORIGIN_ENV = "CGH_ORIGIN"


def normalize_origin(value: str | None) -> str | None:
    """A known origin in lower case, or None for anything else."""
    origin = (value or "").strip().lower()
    return origin if origin in ORIGINS else None


def client_origin() -> str:
    """Origin a CLI process announces to the owner: "hook" when a hook entry
    point set CGH_ORIGIN, else "cli"."""
    import os

    return "hook" if normalize_origin(os.environ.get(ORIGIN_ENV)) == "hook" else "cli"


# Keyed by resolved repo root: one process can serve several repos
# (federation, SDK, tests); a first-caller-wins global handed repo A's
# knowledge DB to repo B. Same pattern as state/findings.py.
_conns: dict[str, sqlite3.Connection] = {}

# This connection is shared across asyncio MCP tool threads, watcher threads,
# and the main owner thread. Sqlite3 in Python forbids cross-thread reuse
# unless check_same_thread=False AND callers serialize themselves. This lock
# guards every operation against the shared connection.
_LOG_LOCK = threading.RLock()


def _locked(fn):
    """Serialize all access to the shared sqlite3 connection."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOG_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def _cache_key(repo_root: str | Path | None) -> str:
    return str((Path(repo_root) if repo_root else Path.cwd()).resolve())


def _get_conn(repo_root: str | Path | None = None) -> sqlite3.Connection:
    key = _cache_key(repo_root)
    conn = _conns.get(key)
    if conn is not None:
        return conn

    # Schema bootstrap below races if two threads first-touch concurrently.
    with _LOG_LOCK:
        conn = _conns.get(key)
        if conn is not None:
            return conn
        return _init_conn(repo_root)


def reset_for_tests() -> None:
    # Close every cached connection. Test helper, mirrors findings.py.
    with _LOG_LOCK:
        for conn in _conns.values():
            try:
                conn.close()
            except Exception:
                pass
        _conns.clear()


def _init_conn(repo_root: str | Path | None = None) -> sqlite3.Connection:
    key = _cache_key(repo_root)
    root = Path(key)
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)

    _conn = sqlite3.connect(str(db_dir / _LOG_FILE), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS call_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   REAL NOT NULL,
            tool        TEXT NOT NULL,
            args        TEXT NOT NULL DEFAULT '{}',
            latency_ms  REAL NOT NULL DEFAULT 0,
            result_size INTEGER NOT NULL DEFAULT 0,
            success     INTEGER NOT NULL DEFAULT 1,
            error       TEXT
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_log_tool ON call_log(tool)
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_log_ts ON call_log(timestamp)
    """)
    # Session-scoped dedup, track which entities a context_for_task /
    # session_context call has already surfaced for a given session_id.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS session_mentions (
            session_id   TEXT NOT NULL,
            entity_kind  TEXT NOT NULL,
            entity_key   TEXT NOT NULL,
            ts           REAL NOT NULL,
            PRIMARY KEY (session_id, entity_kind, entity_key)
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mentions_session
            ON session_mentions(session_id)
    """)
    # Knowledge store, patterns, decisions, gotchas, style preferences,
    # glossary entries. Explicitly written by Claude via the knowledge_*
    # MCP tools. Backed by an FTS5 virtual table for BM25 search.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            tags        TEXT NOT NULL DEFAULT '',
            kind        TEXT NOT NULL DEFAULT 'note',
            file_refs   TEXT NOT NULL DEFAULT '',
            ts          REAL NOT NULL
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge(kind)
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_session ON knowledge(session_id)
    """)
    _conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, body, tags, kind UNINDEXED,
            content='knowledge', content_rowid='id'
        )
    """)
    # Keep the external-content FTS in sync with the knowledge table even
    # when callers bypass the helpers below. Without these triggers a raw
    # DELETE leaves orphan rowids that surface as "missing row N from
    # content table" once queried.
    _conn.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
            INSERT INTO knowledge_fts(rowid, title, body, tags, kind)
                VALUES (new.id, new.title, new.body, new.tags, new.kind);
        END
    """)
    _conn.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags, kind)
                VALUES('delete', old.id, old.title, old.body, old.tags, old.kind);
        END
    """)
    _conn.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags, kind)
                VALUES('delete', old.id, old.title, old.body, old.tags, old.kind);
            INSERT INTO knowledge_fts(rowid, title, body, tags, kind)
                VALUES (new.id, new.title, new.body, new.tags, new.kind);
        END
    """)
    # Supersede links: an entry can replace an older one; superseded
    # entries stop appearing in searches, lists and resume bundles but
    # stay queryable by id. Guarded ALTER for databases created before
    # the column existed.
    try:
        _conn.execute("ALTER TABLE knowledge ADD COLUMN superseded_by INTEGER")
    except sqlite3.OperationalError:
        pass  # column already there
    # Promotion columns: a per-ticket worktree learns something and it is
    # promoted into the main checkout's store at merge. `scope` is 'repo' (true
    # for the repo, promotable) or 'branch' (true only on an unmerged branch, not
    # promoted); the source_* columns and the two timestamps carry provenance so
    # a promoted entry keeps its origin instead of looking native. Guarded ALTERs
    # for databases created before the columns existed. source_worktree and
    # origin_id (the entry's id in the source store) make a re-run recognise
    # what it already carried, and let a revised entry replace its earlier copy.
    for _col, _type in (
        ("scope", "TEXT"),
        ("source_branch", "TEXT"),
        ("source_commit", "TEXT"),
        ("source_pr", "TEXT"),
        ("source_session", "TEXT"),
        ("origin_ts", "REAL"),
        ("promoted_at", "REAL"),
        ("source_worktree", "TEXT"),
        ("origin_id", "INTEGER"),
    ):
        try:
            _conn.execute(f"ALTER TABLE knowledge ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass  # column already there
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_origin "
        "ON knowledge(origin_id, source_worktree)"
    )
    # Who triggered each call (agent, hook, cli, internal) and which repo it
    # served, so usage data separates agent choices from hook traffic and can
    # be aggregated per repo. Rows logged before these columns stay NULL,
    # which reads as "unknown".
    for _col in ("origin", "repo_root"):
        try:
            _conn.execute(f"ALTER TABLE call_log ADD COLUMN {_col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already there
    _conn.commit()
    # Self-heal: if the FTS references rowids that no longer exist, rebuild
    # from the content table. Cheap at open time (runs once per connection).
    # The check is spelled as an INSERT, so sqlite3 opens an implicit
    # transaction for it; end it, or this connection keeps the write lock until
    # its next commit and every other process writing the store (a CLI promote
    # next to a running owner, a hook) times out on "database is locked".
    try:
        _conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES('integrity-check')"
        ).fetchall()
        _conn.commit()
    except sqlite3.DatabaseError:
        try:
            _conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            _conn.commit()
        except sqlite3.DatabaseError:
            pass
    _conns[key] = _conn
    return _conn


# ---------------------------------------------------------------------------
# Session-scoped dedup helpers
# ---------------------------------------------------------------------------


@_locked
def filter_unseen(
    session_id: str,
    entities: list[tuple[str, str]],
    repo_root: str | Path | None = None,
) -> list[tuple[str, str]]:
    """
    Given (kind, key) pairs, return only those NOT yet mentioned in this
    session. Cheap SQL lookup.
    """
    if not session_id or not entities:
        return entities
    conn = _get_conn(repo_root)
    cur = conn.cursor()
    try:
        placeholders = ",".join("(?,?)" for _ in entities)
        flat: list[str] = []
        for kind, key in entities:
            flat.extend((kind, key))
        rows = cur.execute(
            "SELECT entity_kind, entity_key FROM session_mentions "
            f"WHERE session_id = ? AND (entity_kind, entity_key) IN ({placeholders})",
            [session_id, *flat],
        ).fetchall()
        seen = {(r[0], r[1]) for r in rows}
        return [e for e in entities if e not in seen]
    finally:
        cur.close()


@_locked
def record_mentions(
    session_id: str,
    entities: list[tuple[str, str]],
    repo_root: str | Path | None = None,
) -> int:
    """Record (kind, key) pairs as served this session. Returns new count."""
    if not session_id or not entities:
        return 0
    conn = _get_conn(repo_root)
    ts = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO session_mentions(session_id, entity_kind, entity_key, ts) VALUES (?, ?, ?, ?)",
        [(session_id, k, v, ts) for k, v in entities],
    )
    conn.commit()
    return len(entities)


@_locked
def clear_session(session_id: str, repo_root: str | Path | None = None) -> int:
    """Wipe the dedup cache for a specific session (e.g. at session end)."""
    if not session_id:
        return 0
    conn = _get_conn(repo_root)
    cur = conn.execute(
        "DELETE FROM session_mentions WHERE session_id = ?", (session_id,)
    )
    conn.commit()
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Knowledge store, patterns, decisions, gotchas, glossary
# ---------------------------------------------------------------------------


_VALID_KINDS = (
    "pattern",
    "decision",
    "gotcha",
    "style",
    "glossary",
    "note",
    "standing_instruction",
)

# The kinds promotion carries by default: durable, repo-wide learnings. Plain
# notes come too unless the caller opts out; session digests and auto-checkpoint
# markers never promote (they are session state, not knowledge).
_PROMOTABLE_KINDS = (
    "decision",
    "gotcha",
    "pattern",
    "style",
    "glossary",
    "standing_instruction",
)


@_locked
def knowledge_record(
    title: str,
    body: str,
    kind: str = "note",
    tags: list[str] | str = "",
    file_refs: list[str] | str = "",
    session_id: str = "",
    repo_root: str | Path | None = None,
    supersedes: int = 0,
    scope: str = "repo",
    source_branch: str = "",
    source_commit: str = "",
    source_pr: str = "",
    source_session: str = "",
    origin_ts: float | None = None,
    promoted_at: float | None = None,
    source_worktree: str = "",
    origin_id: int | None = None,
) -> int:
    """
    Persist a distilled knowledge entry. Returns the row id.

    kind ∈ {pattern, decision, gotcha, style, glossary, note}.
    tags can be a list or a comma/space-separated string.
    file_refs is similar, canonical paths the entry refers to.
    scope is 'repo' (default, promotable) or 'branch' (true only on an unmerged
    branch, not promoted). The source_* fields, origin_id and promoted_at carry
    provenance when an entry is promoted from another checkout; origin_ts
    defaults to now for a native entry and preserves the source entry's
    timestamp on promotion.
    """
    if kind not in _VALID_KINDS:
        kind = "note"
    if scope not in ("repo", "branch"):
        scope = "repo"
    if isinstance(tags, list):
        tags = ",".join(t.strip() for t in tags if t and t.strip())
    if isinstance(file_refs, list):
        file_refs = ",".join(f.strip() for f in file_refs if f and f.strip())
    conn = _get_conn(repo_root)
    now = time.time()
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, title, body, tags, kind, file_refs, ts, "
        "scope, source_branch, source_commit, source_pr, source_session, origin_ts, "
        "promoted_at, source_worktree, origin_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            title or "",
            body or "",
            tags or "",
            kind,
            file_refs or "",
            now,
            scope,
            source_branch or "",
            source_commit or "",
            source_pr or "",
            source_session or "",
            origin_ts if origin_ts is not None else now,
            promoted_at,
            source_worktree or None,
            origin_id,
        ),
    )
    row_id = cur.lastrowid
    # FTS mirror is now maintained by the AFTER INSERT trigger.
    if supersedes and row_id:
        conn.execute(
            "UPDATE knowledge SET superseded_by = ? WHERE id = ?",
            (int(row_id), int(supersedes)),
        )
    conn.commit()
    return int(row_id or 0)


# Tags that mark session state rather than knowledge: digests, checkpoints
# (manual, compaction and auto) and IBM Bob's auto digests all carry one.
_SESSION_STATE_TAGS = frozenset({"session-digest", "auto-checkpoint", "auto-digest"})


def _supersede_chain(src: sqlite3.Connection) -> dict[int, list[int]]:
    """Map each entry id to the ids it replaced, directly or through a chain."""
    direct: dict[int, list[int]] = {}
    for old_id, new_id in src.execute(
        "SELECT id, superseded_by FROM knowledge WHERE superseded_by IS NOT NULL"
    ).fetchall():
        direct.setdefault(int(new_id), []).append(int(old_id))
    chains: dict[int, list[int]] = {}
    for head in direct:
        seen: list[int] = []
        stack = list(direct[head])
        while stack:
            cur = stack.pop()
            if cur in seen or cur == head:
                continue
            seen.append(cur)
            stack.extend(direct.get(cur, []))
        chains[head] = seen
    return chains


@_locked
def promote_knowledge(
    from_root: str | Path,
    to_root: str | Path,
    *,
    since_ts: float = 0.0,
    kinds: list[str] | None = None,
    include_plain_notes: bool = True,
    source_branch: str = "",
    source_commit: str = "",
    source_pr: str = "",
    source_session: str = "",
    source_worktree: str = "",
    dry_run: bool = False,
) -> dict:
    """Copy durable learnings from one store into another, the case being a
    per-ticket worktree promoted into its main checkout at merge.

    Carried: live (not superseded), ``scope='repo'`` entries (a NULL scope from
    before the column existed counts as repo) of the wanted kinds. Session
    digests and checkpoints never move. ``kinds`` replaces the default set
    outright; without it the durable kinds move, plus plain notes unless
    ``include_plain_notes`` is False.

    Per entry, in order:

    - the same kind, title and body anywhere in the target, live or
      superseded: skipped as a duplicate, so a re-run adds nothing and an
      entry the target has since replaced is not resurrected;
    - it replaced older source entries (``supersedes``) whose copies are live
      in the target, matched by origin id or by content: inserted, and those
      copies are superseded by it (a copy is matched by origin id from this
      same worktree and branch, or by content);
    - otherwise: inserted.

    Every inserted row carries provenance (branch, commit, PR, session,
    worktree, origin id, origin timestamp, promoted_at). ``dry_run`` computes
    the same outcome and writes nothing. Returns counts plus one line per
    considered entry under ``entries``.
    """
    if kinds:
        wanted = set(kinds)
    else:
        wanted = set(_PROMOTABLE_KINDS) | ({"note"} if include_plain_notes else set())
    worktree = source_worktree or str(Path(from_root).resolve())

    src = _get_conn(from_root)
    cols = (
        "id, title, body, kind, tags, file_refs, session_id, ts, scope, superseded_by"
    )
    all_rows = src.execute(f"SELECT {cols} FROM knowledge ORDER BY ts ASC").fetchall()
    by_id = {r[0]: r for r in all_rows}
    chains = _supersede_chain(src)

    tgt = _get_conn(to_root)
    # Every target row, superseded ones included: a re-run must not resurrect
    # an entry the target has since replaced.
    content_rows: dict[tuple, list[tuple[int, bool]]] = {}
    origin_rows: dict[int, list[tuple[int, bool]]] = {}
    for tid, kind, title, body, sup, oid, wt, br in tgt.execute(
        "SELECT id, kind, title, body, superseded_by, origin_id, source_worktree, "
        "source_branch FROM knowledge"
    ).fetchall():
        content_rows.setdefault((kind, title, body), []).append((tid, sup is None))
        if oid is not None and wt == worktree and (br or "") == source_branch:
            origin_rows.setdefault(int(oid), []).append((tid, sup is None))

    copied = superseded = skipped = considered = 0
    entries: list[dict] = []
    next_fake_id = -1  # dry-run stand-in ids, never written
    for row in all_rows:
        sid, title, body, kind, tags, file_refs, sess, ts, scope, sup = row
        if sup is not None:
            continue
        tagset = {t.strip() for t in (tags or "").split(",") if t.strip()}
        if tagset & _SESSION_STATE_TAGS:
            continue
        if (scope or "repo") != "repo":
            continue
        if kind not in wanted:
            continue
        if (ts or 0.0) < since_ts:
            continue
        considered += 1
        content = (kind, title, body)
        line = {"source_id": sid, "kind": kind, "title": title}

        if content in content_rows:
            skipped += 1
            entries.append({**line, "action": "duplicate"})
            continue

        # Live target copies of the entries this one replaced.
        replaces: list[int] = []
        for old_id in chains.get(sid, []):
            for tid, live in origin_rows.get(old_id, []):
                if live and tid not in replaces:
                    replaces.append(tid)
            old = by_id.get(old_id)
            if old is not None:
                for tid, live in content_rows.get((old[3], old[1], old[2]), []):
                    if live and tid not in replaces:
                        replaces.append(tid)

        if dry_run:
            new_id = next_fake_id
            next_fake_id -= 1
        else:
            new_id = knowledge_record(
                title,
                body,
                kind=kind,
                tags=tags or "",
                file_refs=file_refs or "",
                repo_root=to_root,
                scope="repo",
                source_branch=source_branch,
                source_commit=source_commit,
                source_pr=source_pr,
                source_session=source_session or sess or "",
                origin_ts=ts,
                promoted_at=time.time(),
                source_worktree=worktree,
                origin_id=sid,
            )
            if replaces:
                tgt.executemany(
                    "UPDATE knowledge SET superseded_by = ? "
                    "WHERE id = ? AND superseded_by IS NULL",
                    [(new_id, tid) for tid in replaces],
                )
                tgt.commit()
        # Keep the in-memory view current so later rows in this run see it.
        if replaces:
            gone = set(replaces)
            for view in (*content_rows.values(), *origin_rows.values()):
                view[:] = [(rid, live and rid not in gone) for rid, live in view]
        content_rows.setdefault(content, []).append((new_id, True))
        origin_rows.setdefault(sid, []).append((new_id, True))

        if replaces:
            superseded += 1
            entries.append(
                {**line, "action": "superseded", "target_id": new_id,
                 "supersedes": replaces}
            )  # fmt: skip
        else:
            copied += 1
            entries.append({**line, "action": "copied", "target_id": new_id})

    if dry_run:
        for e in entries:
            e.pop("target_id", None)
    return {
        "promoted": copied + superseded,
        "copied": copied,
        "superseded": superseded,
        "skipped_duplicate": skipped,
        "considered": considered,
        "dry_run": dry_run,
        "entries": entries,
    }


def knowledge_export(repo_root: str | Path | None = None) -> list[dict]:
    """Every knowledge row with every column, superseded ones and session
    digests included, oldest first. Used to archive a store before its
    worktree is removed."""
    with _LOG_LOCK:
        conn = _get_conn(repo_root)
        cur = conn.execute("SELECT * FROM knowledge ORDER BY id ASC")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@_locked
def knowledge_search(
    query: str,
    kind: str | None = None,
    limit: int = 10,
    repo_root: str | Path | None = None,
) -> list[dict]:
    """BM25 search over knowledge entries. Graceful LIKE fallback."""
    conn = _get_conn(repo_root)
    out: list[dict] = []
    try:
        sql = (
            f"SELECT {_k_cols('k.')}, rank AS score "
            "FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH ? AND k.superseded_by IS NULL "
        )
        params: list = [query]
        if kind:
            sql += "AND k.kind = ? "
            params.append(kind)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        for row in conn.execute(sql, params).fetchall():
            out.append(_knowledge_row_to_dict(row, score=-row[-1]))
    except sqlite3.OperationalError:
        like = f"%{query}%"
        sql = (
            f"SELECT {_k_cols()} FROM knowledge "
            "WHERE superseded_by IS NULL AND (title LIKE ? OR body LIKE ? OR tags LIKE ?) "
        )
        params = [like, like, like]
        if kind:
            sql += "AND kind = ? "
            params.append(kind)
        sql += "ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        for i, row in enumerate(conn.execute(sql, params).fetchall()):
            out.append(_knowledge_row_to_dict(row, score=1.0 / (i + 1)))
    return out


def knowledge_search_ro(
    db_path: Path, query: str, kind: str | None = None, limit: int = 10
) -> list[dict]:
    """Read-only knowledge search against an arbitrary call_log.db
    (federated children). Fresh connection, LIKE-based on purpose:
    robust against FTS schema drift in the child. Superseded entries
    stay out, like everywhere else."""
    if not db_path.exists():
        return []
    from codegraph.core.utils import ro_sqlite_uri

    try:
        conn = sqlite3.connect(ro_sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return []
    like = f"%{query}%"
    sql = (
        "SELECT id, kind, title, body, tags, file_refs, session_id, ts FROM knowledge "
        "WHERE superseded_by IS NULL AND (title LIKE ? OR body LIKE ? OR tags LIKE ?) "
    )
    params: list = [like, like, like]
    if kind:
        sql += "AND kind = ? "
        params.append(kind)
    sql += "ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        _knowledge_row_to_dict(row, score=1.0 / (i + 1)) for i, row in enumerate(rows)
    ]


@_locked
def knowledge_list(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo_root: str | Path | None = None,
    exclude_tag: str | None = None,
) -> list[dict]:
    """Browse knowledge entries. Filters: kind / tag (substring) / session /
    exclude_tag (substring to omit). Pagination: limit + offset. Caller can
    fetch limit+1 to detect has_more.
    """
    conn = _get_conn(repo_root)
    sql = f"SELECT {_k_cols()} FROM knowledge"
    where: list[str] = ["superseded_by IS NULL"]
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if exclude_tag:
        where.append("tags NOT LIKE ?")
        params.append(f"%{exclude_tag}%")
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    return [
        _knowledge_row_to_dict(row, score=row[7])
        for row in conn.execute(sql, params).fetchall()
    ]


@_locked
def knowledge_count(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    repo_root: str | Path | None = None,
) -> int:
    """Total matching entries, use alongside knowledge_list for pagination."""
    conn = _get_conn(repo_root)
    sql = "SELECT count(*) FROM knowledge"
    # Match knowledge_list, which excludes superseded rows. Without this the
    # count exceeds what list can return, and the pagination it feeds (has_more
    # / next_offset) points past the end.
    where: list[str] = ["superseded_by IS NULL"]
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(conn.execute(sql, params).fetchone()[0])


@_locked
def knowledge_terms(
    min_count: int = 1,
    repo_root: str | Path | None = None,
) -> list[tuple[str, int]]:
    """
    Return the glossary, every tag with its occurrence count, sorted by
    frequency. Acts as the "dict" of knowledge.
    """
    conn = _get_conn(repo_root)
    counts: dict[str, int] = {}
    for (tags_csv,) in conn.execute("SELECT tags FROM knowledge WHERE tags <> ''"):
        for t in tags_csv.split(","):
            t = t.strip().lower()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
    return sorted(
        ((t, n) for t, n in counts.items() if n >= min_count),
        key=lambda kv: (-kv[1], kv[0]),
    )


@_locked
def knowledge_forget(
    entry_id: int,
    repo_root: str | Path | None = None,
) -> bool:
    """Delete a single knowledge entry + its FTS row."""
    conn = _get_conn(repo_root)
    existed = conn.execute(
        "SELECT 1 FROM knowledge WHERE id = ?", (entry_id,)
    ).fetchone()
    if not existed:
        return False
    # FTS sync is handled by the AFTER DELETE trigger.
    conn.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
    conn.commit()
    return True


_K_BASE_COLS = ("id", "kind", "title", "body", "tags", "file_refs", "session_id", "ts")
# Provenance of a promoted entry, read after the base columns. Only rows with
# promoted_at set get a `provenance` object, so native entries keep the shape
# they always had.
_K_PROV_COLS = (
    "promoted_at",
    "source_branch",
    "source_worktree",
    "source_pr",
    "source_commit",
    "source_session",
    "origin_id",
    "origin_ts",
)


def _k_cols(prefix: str = "") -> str:
    return ", ".join(prefix + c for c in (*_K_BASE_COLS, *_K_PROV_COLS))


def _knowledge_row_to_dict(row, score: float = 0.0) -> dict:
    out = {
        "id": row[0],
        "kind": row[1],
        "title": row[2],
        "body": row[3],
        "tags": [t for t in (row[4] or "").split(",") if t],
        "file_refs": [f for f in (row[5] or "").split(",") if f],
        "session_id": row[6],
        "ts": row[7] if len(row) > 7 else 0.0,
        "score": round(score, 4),
    }
    n_base = len(_K_BASE_COLS)
    if len(row) >= n_base + len(_K_PROV_COLS) and row[n_base] is not None:
        prov = dict(
            zip(_K_PROV_COLS, row[n_base : n_base + len(_K_PROV_COLS)], strict=True)
        )
        out["provenance"] = {k: v for k, v in prov.items() if v not in (None, "")}
    return out


@_locked
def log_call(
    tool: str,
    args: dict,
    latency_ms: float,
    result_size: int,
    success: bool = True,
    error: str | None = None,
    repo_root: str | Path | None = None,
    origin: str | None = None,
) -> None:
    """Record a tool call. ``origin`` is one of ORIGINS; anything else is
    stored as NULL (unknown)."""
    conn = _get_conn(repo_root)
    conn.execute(
        "INSERT INTO call_log (timestamp, tool, args, latency_ms, result_size, "
        "success, error, origin, repo_root) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            tool,
            json.dumps(args, default=str)[:2000],
            latency_ms,
            result_size,
            1 if success else 0,
            error,
            normalize_origin(origin),
            _cache_key(repo_root),
        ),
    )
    conn.commit()


@contextmanager
def track_call(
    tool: str,
    args: dict,
    repo_root: str | Path | None = None,
    origin: str | None = "internal",
):
    """
    Context manager that auto-logs a tool call with timing.

    Usage:
        with track_call("symbol_lookup", {"name": "foo"}) as tracker:
            result = do_work()
            tracker["result_size"] = len(result)
    """
    tracker = {"result_size": 0, "error": None, "success": True}
    t0 = time.perf_counter()
    try:
        yield tracker
    except Exception as exc:
        tracker["success"] = False
        tracker["error"] = str(exc)[:500]
        raise
    finally:
        latency = (time.perf_counter() - t0) * 1000
        log_call(
            tool=tool,
            args=args,
            latency_ms=round(latency, 2),
            result_size=tracker["result_size"],
            success=tracker["success"],
            error=tracker["error"],
            repo_root=repo_root,
            origin=origin,
        )


@_locked
def get_stats(repo_root: str | Path | None = None) -> dict:
    """Aggregate call statistics."""
    conn = _get_conn(repo_root)

    total = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    if total == 0:
        return {
            "total_calls": 0,
            "tools": {},
            "period": None,
            "by_origin": {},
            "repo_root": _cache_key(repo_root),
        }

    # Per-tool stats
    rows = conn.execute("""
        SELECT tool,
               COUNT(*) as calls,
               ROUND(AVG(latency_ms), 2) as avg_latency_ms,
               ROUND(MIN(latency_ms), 2) as min_latency_ms,
               ROUND(MAX(latency_ms), 2) as max_latency_ms,
               SUM(result_size) as total_result_bytes,
               SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as errors
        FROM call_log
        GROUP BY tool
        ORDER BY calls DESC
    """).fetchall()

    tools = {}
    for row in rows:
        tools[row[0]] = {
            "calls": row[1],
            "avg_latency_ms": row[2],
            "min_latency_ms": row[3],
            "max_latency_ms": row[4],
            "total_result_bytes": row[5],
            "errors": row[6],
            "by_origin": {},
        }

    # Split by who triggered the call. NULL origin (rows logged before the
    # column existed, or an unrecognised value) is reported as "unknown".
    by_origin: dict[str, int] = {}
    for tool_name, origin, calls in conn.execute(
        "SELECT tool, COALESCE(origin, 'unknown'), COUNT(*) FROM call_log "
        "GROUP BY tool, COALESCE(origin, 'unknown')"
    ).fetchall():
        by_origin[origin] = by_origin.get(origin, 0) + calls
        if tool_name in tools:
            tools[tool_name]["by_origin"][origin] = calls

    # Time range
    first = conn.execute("SELECT MIN(timestamp) FROM call_log").fetchone()[0]
    last = conn.execute("SELECT MAX(timestamp) FROM call_log").fetchone()[0]

    # Errors
    error_count = conn.execute(
        "SELECT COUNT(*) FROM call_log WHERE success = 0"
    ).fetchone()[0]

    # Top queries (most recent 10)
    recent = conn.execute("""
        SELECT tool, args, latency_ms, result_size, success,
               datetime(timestamp, 'unixepoch', 'localtime') as ts, origin
        FROM call_log
        ORDER BY timestamp DESC
        LIMIT 10
    """).fetchall()

    recent_calls = [
        {
            "tool": r[0],
            "args": r[1][:100],
            "latency_ms": r[2],
            "result_size": r[3],
            "success": bool(r[4]),
            "timestamp": r[5],
            "origin": r[6],
        }
        for r in recent
    ]

    return {
        "total_calls": total,
        "error_count": error_count,
        "error_rate": f"{error_count / total * 100:.1f}%" if total > 0 else "0%",
        "period": {
            "first_call": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first)),
            "last_call": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)),
        },
        "tools": tools,
        "recent_calls": recent_calls,
        "by_origin": by_origin,
        "repo_root": _cache_key(repo_root),
    }


@_locked
def get_logs(
    repo_root: str | Path | None = None,
    tool: str | None = None,
    limit: int = 50,
    errors_only: bool = False,
) -> list[dict]:
    """Get raw call logs with optional filters."""
    conn = _get_conn(repo_root)

    where_clauses = []
    params: list = []

    if tool:
        where_clauses.append("tool = ?")
        params.append(tool)
    if errors_only:
        where_clauses.append("success = 0")

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT tool, args, latency_ms, result_size, success, error, "
        f"datetime(timestamp, 'unixepoch', 'localtime') as ts, origin "
        f"FROM call_log {where} ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()

    return [
        {
            "tool": r[0],
            "args": r[1],
            "latency_ms": r[2],
            "result_size": r[3],
            "success": bool(r[4]),
            "error": r[5],
            "timestamp": r[6],
            "origin": r[7],
        }
        for r in rows
    ]


@_locked
def clear_logs(repo_root: str | Path | None = None) -> int:
    """Clear all call logs. Returns count of deleted rows."""
    conn = _get_conn(repo_root)
    count = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    conn.execute("DELETE FROM call_log")
    conn.commit()
    return count
