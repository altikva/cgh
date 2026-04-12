# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Full-text search over code symbols using BM25 ranking.
#              Backed by SQLite FTS5 for fast substring + relevance search.

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_DB_DIR = ".codegraph"
_FTS_FILE = "fts.db"


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
    conn = sqlite3.connect(str(db_path))
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
    conn.execute(
        "INSERT OR REPLACE INTO symbols (sym_id, kind, name, file_path, start_line, end_line, docstring) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sym_id, kind, name, file_path, start_line, end_line, docstring),
    )
    # Rebuild FTS for this row
    rowid = conn.execute("SELECT rowid FROM symbols WHERE sym_id = ?", (sym_id,)).fetchone()
    if rowid:
        conn.execute(
            "INSERT OR REPLACE INTO symbols_fts(rowid, name, docstring) VALUES (?, ?, ?)",
            (rowid[0], _tokenize(name), docstring),
        )


def delete_file_symbols(conn: sqlite3.Connection, file_path: str) -> None:
    """Remove all symbols for a file from both tables."""
    rows = conn.execute("SELECT rowid FROM symbols WHERE file_path = ?", (file_path,)).fetchall()
    for (rowid,) in rows:
        conn.execute(
            "INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring) VALUES('delete', ?, '', '')",
            (rowid,),
        )
    conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))


def commit(conn: sqlite3.Connection) -> None:
    """Commit pending changes."""
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
