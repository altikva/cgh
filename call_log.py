# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Call logger for codegraph MCP tools.
#              Logs every tool invocation with args, result size, latency.
#              Backed by SQLite for persistence and fast aggregation.

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_DB_DIR = ".codegraph"
_LOG_FILE = "call_log.db"

_conn: sqlite3.Connection | None = None


def _get_conn(repo_root: str | Path | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn

    root = Path(repo_root) if repo_root else Path.cwd()
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)

    _conn = sqlite3.connect(str(db_dir / _LOG_FILE))
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
    # Session-scoped dedup — track which entities a context_for_task /
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
    # Knowledge store — patterns, decisions, gotchas, style preferences,
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
    _conn.commit()
    # Self-heal: if the FTS references rowids that no longer exist, rebuild
    # from the content table. Cheap at open time (runs once per connection).
    try:
        _conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('integrity-check')").fetchall()
    except sqlite3.DatabaseError:
        try:
            _conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            _conn.commit()
        except sqlite3.DatabaseError:
            pass
    return _conn


# ---------------------------------------------------------------------------
# Session-scoped dedup helpers
# ---------------------------------------------------------------------------


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


def clear_session(session_id: str, repo_root: str | Path | None = None) -> int:
    """Wipe the dedup cache for a specific session (e.g. at session end)."""
    if not session_id:
        return 0
    conn = _get_conn(repo_root)
    cur = conn.execute("DELETE FROM session_mentions WHERE session_id = ?", (session_id,))
    conn.commit()
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Knowledge store — patterns, decisions, gotchas, glossary
# ---------------------------------------------------------------------------


_VALID_KINDS = ("pattern", "decision", "gotcha", "style", "glossary", "note")


def knowledge_record(
    title: str,
    body: str,
    kind: str = "note",
    tags: list[str] | str = "",
    file_refs: list[str] | str = "",
    session_id: str = "",
    repo_root: str | Path | None = None,
) -> int:
    """
    Persist a distilled knowledge entry. Returns the row id.

    kind ∈ {pattern, decision, gotcha, style, glossary, note}.
    tags can be a list or a comma/space-separated string.
    file_refs is similar — canonical paths the entry refers to.
    """
    if kind not in _VALID_KINDS:
        kind = "note"
    if isinstance(tags, list):
        tags = ",".join(t.strip() for t in tags if t and t.strip())
    if isinstance(file_refs, list):
        file_refs = ",".join(f.strip() for f in file_refs if f and f.strip())
    conn = _get_conn(repo_root)
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, title, body, tags, kind, file_refs, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, title or "", body or "", tags or "", kind, file_refs or "", time.time()),
    )
    row_id = cur.lastrowid
    # FTS mirror is now maintained by the AFTER INSERT trigger.
    conn.commit()
    return int(row_id or 0)


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
            "SELECT k.id, k.kind, k.title, k.body, k.tags, k.file_refs, k.session_id, k.ts, rank AS score "
            "FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH ? "
        )
        params: list = [query]
        if kind:
            sql += "AND k.kind = ? "
            params.append(kind)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        for row in conn.execute(sql, params).fetchall():
            out.append(_knowledge_row_to_dict(row, score=-row[8]))
    except sqlite3.OperationalError:
        like = f"%{query}%"
        sql = (
            "SELECT id, kind, title, body, tags, file_refs, session_id, ts FROM knowledge "
            "WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? "
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


def knowledge_list(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo_root: str | Path | None = None,
) -> list[dict]:
    """Browse knowledge entries. Filters: kind / tag (substring) / session.
    Pagination: limit + offset. Caller can fetch limit+1 to detect has_more.
    """
    conn = _get_conn(repo_root)
    sql = "SELECT id, kind, title, body, tags, file_refs, session_id, ts FROM knowledge"
    where: list[str] = []
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
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    return [_knowledge_row_to_dict(row, score=row[7]) for row in conn.execute(sql, params).fetchall()]


def knowledge_count(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    repo_root: str | Path | None = None,
) -> int:
    """Total matching entries — use alongside knowledge_list for pagination."""
    conn = _get_conn(repo_root)
    sql = "SELECT count(*) FROM knowledge"
    where: list[str] = []
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


def knowledge_terms(
    min_count: int = 1,
    repo_root: str | Path | None = None,
) -> list[tuple[str, int]]:
    """
    Return the glossary — every tag with its occurrence count, sorted by
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


def knowledge_forget(
    entry_id: int,
    repo_root: str | Path | None = None,
) -> bool:
    """Delete a single knowledge entry + its FTS row."""
    conn = _get_conn(repo_root)
    existed = conn.execute("SELECT 1 FROM knowledge WHERE id = ?", (entry_id,)).fetchone()
    if not existed:
        return False
    # FTS sync is handled by the AFTER DELETE trigger.
    conn.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
    conn.commit()
    return True


def _knowledge_row_to_dict(row, score: float = 0.0) -> dict:
    return {
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


def log_call(
    tool: str,
    args: dict,
    latency_ms: float,
    result_size: int,
    success: bool = True,
    error: str | None = None,
    repo_root: str | Path | None = None,
) -> None:
    """Record a tool call."""
    conn = _get_conn(repo_root)
    conn.execute(
        "INSERT INTO call_log (timestamp, tool, args, latency_ms, result_size, success, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            tool,
            json.dumps(args, default=str)[:2000],
            latency_ms,
            result_size,
            1 if success else 0,
            error,
        ),
    )
    conn.commit()


@contextmanager
def track_call(tool: str, args: dict, repo_root: str | Path | None = None):
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
        )


def get_stats(repo_root: str | Path | None = None) -> dict:
    """Aggregate call statistics."""
    conn = _get_conn(repo_root)

    total = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    if total == 0:
        return {"total_calls": 0, "tools": {}, "period": None}

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
        }

    # Time range
    first = conn.execute("SELECT MIN(timestamp) FROM call_log").fetchone()[0]
    last = conn.execute("SELECT MAX(timestamp) FROM call_log").fetchone()[0]

    # Errors
    error_count = conn.execute("SELECT COUNT(*) FROM call_log WHERE success = 0").fetchone()[0]

    # Top queries (most recent 10)
    recent = conn.execute("""
        SELECT tool, args, latency_ms, result_size, success,
               datetime(timestamp, 'unixepoch', 'localtime') as ts
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
    }


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
        f"datetime(timestamp, 'unixepoch', 'localtime') as ts "
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
        }
        for r in rows
    ]


def clear_logs(repo_root: str | Path | None = None) -> int:
    """Clear all call logs. Returns count of deleted rows."""
    conn = _get_conn(repo_root)
    count = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    conn.execute("DELETE FROM call_log")
    conn.commit()
    return count
