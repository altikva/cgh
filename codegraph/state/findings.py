# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Finding store: what scanners know about each file
#              (pii.email, secret.aws_key, confidential, summary, ...).
#              Deliberately SQLite (findings.db, WAL), NOT the graph DB:
#              the graph backend holds an exclusive write lock while an
#              owner is alive, and findings must stay readable at that
#              exact moment (enforcement hooks, CLI, gates) as well as
#              when no owner runs. WAL serves concurrent readers while
#              the owner writes.

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_DB_DIR = ".codegraph"
_DB_FILE = "findings.db"

# One shared RW connection per repo root, guarded like call_log's: the
# owner touches this from watcher threads and MCP tool threads at once.
_LOCK = threading.RLock()
_CONNS: dict[str, sqlite3.Connection] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY,
    file_path   TEXT NOT NULL,
    scanner     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    line        INTEGER NOT NULL DEFAULT 0,
    severity    TEXT NOT NULL DEFAULT 'info',
    blob_sha    TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file_path);
CREATE INDEX IF NOT EXISTS idx_findings_key ON findings(key);
CREATE INDEX IF NOT EXISTS idx_findings_scanner ON findings(scanner);
"""


def findings_db_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / _DB_DIR / _DB_FILE


def _get_conn(repo_root: str | Path) -> sqlite3.Connection:
    root_key = str(Path(repo_root).resolve())
    with _LOCK:
        conn = _CONNS.get(root_key)
        if conn is not None:
            return conn
        db_path = findings_db_path(repo_root)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        _CONNS[root_key] = conn
        return conn


def record_findings(
    repo_root: str | Path,
    file_path: str,
    scanner: str,
    findings: list,
    blob_sha: str = "",
) -> int:
    """Replace this scanner's findings for ``file_path`` with ``findings``
    (items expose .key/.value/.line/.severity). Returns the row count.
    Recording an empty list still updates the marker row set, which is
    how ``already_scanned`` knows a clean file was scanned at this SHA.
    """
    now = time.time()
    with _LOCK:
        conn = _get_conn(repo_root)
        conn.execute(
            "DELETE FROM findings WHERE file_path = ? AND scanner = ?",
            (file_path, scanner),
        )
        rows = [
            (
                file_path,
                scanner,
                f.key,
                str(f.value),
                int(getattr(f, "line", 0) or 0),
                getattr(f, "severity", "info") or "info",
                blob_sha,
                now,
            )
            for f in findings
        ]
        if not rows:
            # Marker row: scanned, nothing found. key '' never matches a
            # real key prefix and is filtered out of every query below.
            rows = [(file_path, scanner, "", "", 0, "info", blob_sha, now)]
        conn.executemany(
            "INSERT INTO findings "
            "(file_path, scanner, key, value, line, severity, blob_sha, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(findings)


def already_scanned(
    repo_root: str | Path, file_path: str, scanner: str, blob_sha: str
) -> bool:
    """True when this scanner already ran on this exact content."""
    if not blob_sha:
        return False
    with _LOCK:
        conn = _get_conn(repo_root)
        row = conn.execute(
            "SELECT 1 FROM findings WHERE file_path = ? AND scanner = ? "
            "AND blob_sha = ? LIMIT 1",
            (file_path, scanner, blob_sha),
        ).fetchone()
        return row is not None


def findings_for_file(
    repo_root: str | Path, file_path: str, key_prefix: str = ""
) -> list[dict]:
    return query_findings(
        repo_root, key_prefix=key_prefix, file_path=file_path, limit=1000
    )


def query_findings(
    repo_root: str | Path,
    key_prefix: str = "",
    severity: str = "",
    file_path: str = "",
    limit: int = 200,
) -> list[dict]:
    """Findings matching the filters, newest first. The empty-key marker
    rows (scanned, nothing found) never show up here."""
    db_path = findings_db_path(repo_root)
    if not db_path.exists():
        return []
    clauses = ["key != ''"]
    params: list = []
    if key_prefix:
        clauses.append("key LIKE ?")
        params.append(key_prefix + "%")
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if file_path:
        clauses.append("file_path = ?")
        params.append(file_path)
    params.append(int(limit))
    with _LOCK:
        conn = _get_conn(repo_root)
        rows = conn.execute(
            "SELECT file_path, scanner, key, value, line, severity FROM findings "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id LIMIT ?",
            params,
        ).fetchall()
    return [
        {
            "file": r[0],
            "scanner": r[1],
            "key": r[2],
            "value": r[3],
            "line": r[4],
            "severity": r[5],
        }
        for r in rows
    ]


def query_findings_ro(
    db_path: Path, key_prefix: str = "", limit: int = 200
) -> list[dict]:
    """Read-only query against an arbitrary findings.db (federated
    children). Fresh connection, released immediately."""
    if not db_path.exists():
        return []
    from codegraph.core.utils import ro_sqlite_uri

    clauses = ["key != ''"]
    params: list = []
    if key_prefix:
        clauses.append("key LIKE ?")
        params.append(key_prefix + "%")
    params.append(int(limit))
    try:
        conn = sqlite3.connect(ro_sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT file_path, scanner, key, value, line, severity FROM findings "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id LIMIT ?",
            params,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "file": r[0],
            "scanner": r[1],
            "key": r[2],
            "value": r[3],
            "line": r[4],
            "severity": r[5],
        }
        for r in rows
    ]


def purge_file_findings(repo_root: str | Path, file_path: str) -> None:
    """Drop every finding for a file (deleted or about to be re-scanned)."""
    db_path = findings_db_path(repo_root)
    if not db_path.exists():
        return
    with _LOCK:
        conn = _get_conn(repo_root)
        conn.execute("DELETE FROM findings WHERE file_path = ?", (file_path,))
        conn.commit()


def reset_for_tests() -> None:
    """Close and drop every cached connection. Test helper."""
    with _LOCK:
        for conn in _CONNS.values():
            try:
                conn.close()
            except Exception:
                pass
        _CONNS.clear()
