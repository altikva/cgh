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
    _conn.commit()
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
