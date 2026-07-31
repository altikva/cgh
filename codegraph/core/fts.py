# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Full-text search over code symbols using BM25 ranking.
#              Backed by SQLite FTS5 for fast substring + relevance search.

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_DB_DIR = ".codegraph"
_FTS_FILE = "fts.db"

# The cached connection (see indexer._get_fts) is shared across watcher Timer
# threads, MCP tool threads, and the main owner thread. SQLite forbids that
# unless check_same_thread=False AND callers serialize their own writes.
# This lock guards every operation against the shared connection.
_FTS_LOCK = threading.RLock()


@dataclass
class FTSResult:
    kind: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str
    score: float


def get_fts_conn(repo_root: str | Path | None = None) -> sqlite3.Connection:
    """Open (or create) the FTS SQLite database."""
    root = Path(repo_root) if repo_root else Path.cwd()
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)

    db_path = db_dir / _FTS_FILE
    # check_same_thread=False because this connection is cached and reused
    # from watcher Timer threads + MCP tool threads. We serialize writes
    # ourselves via _FTS_LOCK.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            sym_id      TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,
            name        TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            start_line  INTEGER NOT NULL,
            end_line    INTEGER NOT NULL DEFAULT 0,
            docstring   TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            name, docstring, content='symbols', content_rowid='rowid'
        )
    """)
    # Memory entries, indexed from ~/.claude/projects/<slug>/memory/
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            path        TEXT PRIMARY KEY,
            kind        TEXT NOT NULL DEFAULT 'other',
            title       TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            mtime       REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            title, body, kind UNINDEXED,
            content='memory_entries', content_rowid='rowid'
        )
    """)
    # Plan documents, indexed from ~/.claude/plans/
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plan_entries (
            path        TEXT PRIMARY KEY,
            slug        TEXT NOT NULL DEFAULT '',
            agent_id    TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            mtime       REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS plan_fts USING fts5(
            title, body, slug UNINDEXED, agent_id UNINDEXED,
            content='plan_entries', content_rowid='rowid'
        )
    """)
    conn.commit()
    return conn


def upsert_symbol(
    conn: sqlite3.Connection,
    sym_id: str,
    kind: str,
    name: str,
    file_path: str,
    start_line: int,
    end_line: int = 0,
    docstring: str = "",
) -> None:
    """Insert or replace a symbol in both the main table and FTS index."""
    with _FTS_LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO symbols (sym_id, kind, name, file_path, start_line, end_line, docstring) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sym_id, kind, name, file_path, start_line, end_line, docstring),
        )
        rowid = conn.execute("SELECT rowid FROM symbols WHERE sym_id = ?", (sym_id,)).fetchone()
        if rowid:
            conn.execute(
                "INSERT OR REPLACE INTO symbols_fts(rowid, name, docstring) VALUES (?, ?, ?)",
                (rowid[0], _tokenize(name), docstring),
            )


def delete_file_symbols(conn: sqlite3.Connection, file_path: str) -> None:
    """Remove all symbols for a file from both tables."""
    with _FTS_LOCK:
        rows = conn.execute("SELECT rowid FROM symbols WHERE file_path = ?", (file_path,)).fetchall()
        for (rowid,) in rows:
            conn.execute(
                "INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring) VALUES('delete', ?, '', '')",
                (rowid,),
            )
        conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))


def commit(conn: sqlite3.Connection) -> None:
    """Commit pending changes."""
    with _FTS_LOCK:
        conn.commit()


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 15,
    kind_filter: str | None = None,
) -> list[FTSResult]:
    """
    Search symbols by name or docstring content using BM25 ranking.
    Falls back to LIKE search if FTS5 match fails.
    """
    results = []

    # Try FTS5 first
    tokenized = _tokenize(query)
    try:
        sql = (
            "SELECT s.kind, s.name, s.file_path, s.start_line, s.end_line, s.docstring, "
            "rank AS score "
            "FROM symbols_fts f "
            "JOIN symbols s ON s.rowid = f.rowid "
            "WHERE symbols_fts MATCH ? "
        )
        params: list = [tokenized]
        if kind_filter:
            sql += "AND s.kind = ? "
            params.append(kind_filter)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)

        with _FTS_LOCK:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            results.append(
                FTSResult(
                    kind=row[0],
                    name=row[1],
                    file_path=row[2],
                    start_line=row[3],
                    end_line=row[4],
                    docstring=row[5][:200] if row[5] else "",
                    score=abs(row[6]) if row[6] else 0.0,
                )
            )
    except Exception:
        pass

    # Fallback: LIKE search if FTS returned nothing
    if not results:
        sql = (
            "SELECT kind, name, file_path, start_line, end_line, docstring "
            "FROM symbols WHERE name LIKE ? OR docstring LIKE ? "
        )
        params_like: list = [f"%{query}%", f"%{query}%"]
        if kind_filter:
            sql += "AND kind = ? "
            params_like.append(kind_filter)
        sql += "LIMIT ?"
        params_like.append(limit)

        with _FTS_LOCK:
            rows = conn.execute(sql, params_like).fetchall()
        for i, row in enumerate(rows):
            results.append(
                FTSResult(
                    kind=row[0],
                    name=row[1],
                    file_path=row[2],
                    start_line=row[3],
                    end_line=row[4],
                    docstring=row[5][:200] if row[5] else "",
                    score=1.0 / (i + 1),
                )
            )

    return results


# ---------------------------------------------------------------------------
# Memory + Plan helpers (Phase A/B of the Claude Code integration)
# ---------------------------------------------------------------------------


@dataclass
class MemoryHit:
    path: str
    kind: str
    title: str
    snippet: str
    score: float


@dataclass
class PlanHit:
    path: str
    slug: str
    agent_id: str
    title: str
    snippet: str
    score: float


def upsert_memory_entry(
    conn: sqlite3.Connection,
    path: str,
    kind: str,
    title: str,
    body: str,
    mtime: float,
) -> None:
    """Insert or replace a memory entry in both main table and FTS index."""
    with _FTS_LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO memory_entries(path, kind, title, body, mtime) VALUES (?, ?, ?, ?, ?)",
            (path, kind or "other", title or "", body or "", mtime),
        )
        rowid = conn.execute("SELECT rowid FROM memory_entries WHERE path = ?", (path,)).fetchone()
        if rowid:
            conn.execute(
                "INSERT OR REPLACE INTO memory_fts(rowid, title, body, kind) VALUES (?, ?, ?, ?)",
                (rowid[0], title or "", body or "", kind or "other"),
            )


def delete_memory_entry(conn: sqlite3.Connection, path: str) -> None:
    with _FTS_LOCK:
        rowid = conn.execute("SELECT rowid FROM memory_entries WHERE path = ?", (path,)).fetchone()
        if rowid:
            conn.execute(
                "INSERT INTO memory_fts(memory_fts, rowid, title, body, kind) VALUES('delete', ?, '', '', '')",
                (rowid[0],),
            )
        conn.execute("DELETE FROM memory_entries WHERE path = ?", (path,))


def memory_search(
    conn: sqlite3.Connection,
    query: str,
    kind: str | None = None,
    limit: int = 10,
) -> list[MemoryHit]:
    """BM25 search over memory entries. Returns hits ordered by relevance."""
    out: list[MemoryHit] = []
    try:
        sql = (
            "SELECT m.path, m.kind, m.title, m.body, rank AS score "
            "FROM memory_fts f JOIN memory_entries m ON m.rowid = f.rowid "
            "WHERE memory_fts MATCH ? "
        )
        params: list = [_tokenize(query)]
        if kind:
            sql += "AND m.kind = ? "
            params.append(kind)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        with _FTS_LOCK:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            out.append(
                MemoryHit(
                    path=row[0],
                    kind=row[1],
                    title=row[2],
                    snippet=(row[3] or "")[:240],
                    score=-row[4],  # BM25 returns negative values; higher = better
                )
            )
    except sqlite3.OperationalError:
        # Fallback: LIKE search if FTS chokes
        like = f"%{query}%"
        sql = "SELECT path, kind, title, body FROM memory_entries WHERE title LIKE ? OR body LIKE ? "
        params = [like, like]
        if kind:
            sql += "AND kind = ? "
            params.append(kind)
        sql += "LIMIT ?"
        params.append(limit)
        with _FTS_LOCK:
            rows = conn.execute(sql, params).fetchall()
        for i, row in enumerate(rows):
            out.append(
                MemoryHit(
                    path=row[0],
                    kind=row[1],
                    title=row[2],
                    snippet=(row[3] or "")[:240],
                    score=1.0 / (i + 1),
                )
            )
    return out


def list_memory_entries(conn: sqlite3.Connection, kind: str | None = None) -> list[MemoryHit]:
    """All memory entries, newest first, cheap index read."""
    sql = "SELECT path, kind, title, body, mtime FROM memory_entries"
    params: list = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY mtime DESC"
    with _FTS_LOCK:
        rows = conn.execute(sql, params).fetchall()
    return [
        MemoryHit(path=row[0], kind=row[1], title=row[2], snippet=(row[3] or "")[:240], score=row[4]) for row in rows
    ]


def upsert_plan_entry(
    conn: sqlite3.Connection,
    path: str,
    slug: str,
    agent_id: str,
    title: str,
    body: str,
    mtime: float,
) -> None:
    with _FTS_LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO plan_entries(path, slug, agent_id, title, body, mtime) VALUES (?, ?, ?, ?, ?, ?)",
            (path, slug or "", agent_id or "", title or "", body or "", mtime),
        )
        rowid = conn.execute("SELECT rowid FROM plan_entries WHERE path = ?", (path,)).fetchone()
        if rowid:
            conn.execute(
                "INSERT OR REPLACE INTO plan_fts(rowid, title, body, slug, agent_id) VALUES (?, ?, ?, ?, ?)",
                (rowid[0], title or "", body or "", slug or "", agent_id or ""),
            )


def delete_plan_entry(conn: sqlite3.Connection, path: str) -> None:
    with _FTS_LOCK:
        rowid = conn.execute("SELECT rowid FROM plan_entries WHERE path = ?", (path,)).fetchone()
        if rowid:
            conn.execute(
                "INSERT INTO plan_fts(plan_fts, rowid, title, body, slug, agent_id) "
                "VALUES('delete', ?, '', '', '', '')",
                (rowid[0],),
            )
        conn.execute("DELETE FROM plan_entries WHERE path = ?", (path,))


def plan_search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[PlanHit]:
    """BM25 search over plan files."""
    out: list[PlanHit] = []
    try:
        with _FTS_LOCK:
            rows = conn.execute(
                "SELECT p.path, p.slug, p.agent_id, p.title, p.body, rank AS score "
                "FROM plan_fts f JOIN plan_entries p ON p.rowid = f.rowid "
                "WHERE plan_fts MATCH ? ORDER BY rank LIMIT ?",
                (_tokenize(query), limit),
            ).fetchall()
        for row in rows:
            out.append(
                PlanHit(
                    path=row[0],
                    slug=row[1],
                    agent_id=row[2],
                    title=row[3],
                    snippet=(row[4] or "")[:240],
                    score=-row[5],
                )
            )
    except sqlite3.OperationalError:
        like = f"%{query}%"
        with _FTS_LOCK:
            rows = conn.execute(
                "SELECT path, slug, agent_id, title, body FROM plan_entries WHERE title LIKE ? OR body LIKE ? LIMIT ?",
                (like, like, limit),
            ).fetchall()
        for i, row in enumerate(rows):
            out.append(
                PlanHit(
                    path=row[0],
                    slug=row[1],
                    agent_id=row[2],
                    title=row[3],
                    snippet=(row[4] or "")[:240],
                    score=1.0 / (i + 1),
                )
            )
    return out


def list_plan_entries(conn: sqlite3.Connection, agent_only: bool = False, limit: int = 50) -> list[PlanHit]:
    sql = "SELECT path, slug, agent_id, title, body, mtime FROM plan_entries"
    params: list = []
    if agent_only:
        sql += " WHERE agent_id <> ''"
    sql += " ORDER BY mtime DESC LIMIT ?"
    params.append(limit)
    with _FTS_LOCK:
        rows = conn.execute(sql, params).fetchall()
    return [
        PlanHit(
            path=row[0],
            slug=row[1],
            agent_id=row[2],
            title=row[3],
            snippet=(row[4] or "")[:240],
            score=row[5],
        )
        for row in rows
    ]


def _tokenize(name: str) -> str:
    """
    Split PascalCase and snake_case into space-separated tokens for FTS.
    e.g., "DonationHandler" → "Donation Handler"
          "parse_python" → "parse python"
    """
    # Split PascalCase
    tokens = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Split snake_case
    tokens = tokens.replace("_", " ")
    # Split dots
    tokens = tokens.replace(".", " ")
    return tokens.strip()
