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

# One authoritative DDL per external-content FTS index. get_fts_conn creates
# them (with IF NOT EXISTS); rebuild_fts_indexes drops and recreates them from
# the same string, so the two paths never drift apart. Every entry has a
# `content=`/`content_rowid=` pair: the index is derived from a base table and
# must be kept in sync via the FTS5 'delete' / 'insert' commands, never by a
# bare INSERT OR REPLACE that changes the base rowid (that orphans postings and
# eventually corrupts the index, which is what rebuild_fts_indexes recovers).
_FTS_DDL = {
    "symbols_fts": (
        "CREATE VIRTUAL TABLE symbols_fts USING fts5("
        "name, docstring, content='symbols', content_rowid='rowid')"
    ),
    "symbols_tri": (
        "CREATE VIRTUAL TABLE symbols_tri USING fts5("
        "name, docstring, content='symbols', content_rowid='rowid', "
        "tokenize='trigram')"
    ),
    "memory_fts": (
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "title, body, kind UNINDEXED, content='memory_entries', "
        "content_rowid='rowid')"
    ),
    "memory_tri": (
        "CREATE VIRTUAL TABLE memory_tri USING fts5("
        "title, body, kind UNINDEXED, content='memory_entries', "
        "content_rowid='rowid', tokenize='trigram')"
    ),
    "plan_fts": (
        "CREATE VIRTUAL TABLE plan_fts USING fts5("
        "title, body, slug UNINDEXED, agent_id UNINDEXED, "
        "content='plan_entries', content_rowid='rowid')"
    ),
    "plan_tri": (
        "CREATE VIRTUAL TABLE plan_tri USING fts5("
        "title, body, slug UNINDEXED, agent_id UNINDEXED, "
        "content='plan_entries', content_rowid='rowid', tokenize='trigram')"
    ),
}


def _is_corrupt(exc: Exception) -> bool:
    """True for the SQLite errors that mean the FTS index on disk is
    inconsistent and needs rebuilding, not a logic bug to propagate."""
    m = str(exc).lower()
    return "malformed" in m or "disk image" in m or "corrupt" in m


def rebuild_fts_indexes(conn: sqlite3.Connection) -> None:
    """Drop and rebuild every external-content FTS/trigram index from its
    content table. Recovers a 'database disk image is malformed' on the
    derived index without touching the content rows (symbols / memory /
    plan stay); only the index is rebuilt. Safe to call whenever a
    corrupt-index error surfaces."""
    with _FTS_LOCK:
        for name, ddl in _FTS_DDL.items():
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            conn.execute(ddl)
            conn.execute(f"INSERT INTO {name}({name}) VALUES('rebuild')")
        conn.commit()


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
    # The trigram indexes (symbols_tri etc.) split identifiers into 3-grams
    # so a fragment inside an identifier ("andl" in DonationHandler) matches
    # where the word tokenizer never emits it; fts_search fuses the two with
    # RRF. Content tables come first, then every FTS index from the single
    # _FTS_DDL source (same strings rebuild_fts_indexes uses).
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
    for ddl in _FTS_DDL.values():
        conn.execute(
            ddl.replace(
                "CREATE VIRTUAL TABLE ", "CREATE VIRTUAL TABLE IF NOT EXISTS ", 1
            )
        )
    _backfill_trigram(conn)
    conn.commit()
    return conn


def _backfill_trigram(conn: sqlite3.Connection) -> None:
    """Populate a trigram index from its content table the first time it
    appears (an index built before trigram support). Rebuild reads the
    raw columns straight from the content table."""
    pairs = [
        ("symbols_tri", "symbols"),
        ("memory_tri", "memory_entries"),
        ("plan_tri", "plan_entries"),
    ]
    with _FTS_LOCK:
        for tri, content in pairs:
            try:
                have = conn.execute(f"SELECT count(*) FROM {tri}").fetchone()[0]
                total = conn.execute(f"SELECT count(*) FROM {content}").fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if total and not have:
                conn.execute(f"INSERT INTO {tri}({tri}) VALUES('rebuild')")


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
        # REPLACE on the content table gives the row a NEW rowid, so first
        # remove the old rowid's postings from each external-content index
        # (an FTS5 'delete' with the exact indexed values). Skipping this
        # orphans the old postings and eventually corrupts the index.
        old = conn.execute(
            "SELECT rowid, name, docstring FROM symbols WHERE sym_id = ?",
            (sym_id,),
        ).fetchone()
        if old:
            o_rowid, o_name, o_doc = old
            conn.execute(
                "INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring) "
                "VALUES('delete', ?, ?, ?)",
                (o_rowid, _tokenize(o_name), o_doc),
            )
            conn.execute(
                "INSERT INTO symbols_tri(symbols_tri, rowid, name, docstring) "
                "VALUES('delete', ?, ?, ?)",
                (o_rowid, o_name, o_doc),
            )
        conn.execute(
            "INSERT OR REPLACE INTO symbols (sym_id, kind, name, file_path, start_line, end_line, docstring) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sym_id, kind, name, file_path, start_line, end_line, docstring),
        )
        rowid = conn.execute(
            "SELECT rowid FROM symbols WHERE sym_id = ?", (sym_id,)
        ).fetchone()
        if rowid:
            conn.execute(
                "INSERT INTO symbols_fts(rowid, name, docstring) VALUES (?, ?, ?)",
                (rowid[0], _tokenize(name), docstring),
            )
            # Raw name here: the trigram tokenizer wants the original
            # identifier, not the word-split form.
            conn.execute(
                "INSERT INTO symbols_tri(rowid, name, docstring) VALUES (?, ?, ?)",
                (rowid[0], name, docstring),
            )


def delete_file_symbols(conn: sqlite3.Connection, file_path: str) -> None:
    """Remove all symbols for a file from both tables.

    External-content FTS5 'delete' must be handed the exact values that
    were indexed for that rowid, not empty strings, or the index is left
    corrupt. symbols_fts stored the tokenized name, symbols_tri the raw
    name, so each delete replays its own form.
    """
    with _FTS_LOCK:
        rows = conn.execute(
            "SELECT rowid, name, docstring FROM symbols WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        # If the index is already malformed (an older build orphaned
        # postings), the 'delete' below raises. Rebuild the index from the
        # content table once, then retry, rather than crashing the reindex.
        for attempt in range(2):
            try:
                for rowid, name, docstring in rows:
                    conn.execute(
                        "INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring) "
                        "VALUES('delete', ?, ?, ?)",
                        (rowid, _tokenize(name), docstring),
                    )
                    conn.execute(
                        "INSERT INTO symbols_tri(symbols_tri, rowid, name, docstring) "
                        "VALUES('delete', ?, ?, ?)",
                        (rowid, name, docstring),
                    )
                break
            except sqlite3.DatabaseError as exc:
                if attempt == 0 and _is_corrupt(exc):
                    rebuild_fts_indexes(conn)
                    continue
                raise
        conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))


def commit(conn: sqlite3.Connection) -> None:
    """Commit pending changes."""
    with _FTS_LOCK:
        conn.commit()


def _rrf(rank_lists: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion: merge several ranked rowid lists into one
    ordering. A rowid ranked high in either list rises; agreement across
    lists compounds. k dampens the top ranks (the standard 60)."""
    score: dict[int, float] = {}
    for lst in rank_lists:
        for rank, rowid in enumerate(lst):
            score[rowid] = score.get(rowid, 0.0) + 1.0 / (k + rank)
    return sorted(score, key=lambda r: score[r], reverse=True)


def _match_rowids(
    conn: sqlite3.Connection, table: str, match: str, cap: int
) -> list[int]:
    """Rowids of a MATCH, best rank first. Empty on any FTS error."""
    try:
        with _FTS_LOCK:
            rows = conn.execute(
                f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
                (match, cap),
            ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 15,
    kind_filter: str | None = None,
) -> list[FTSResult]:
    """
    Search symbols by name or docstring, fusing a tokenized BM25 ranking
    with a trigram substring ranking (RRF). Falls back to LIKE if both
    FTS indexes fail.
    """
    results = []

    # Fuse the word-tokenized BM25 ranking with the trigram substring
    # ranking. Either alone misses cases the other catches: BM25 nails
    # whole words, trigram nails fragments inside identifiers.
    tokenized = _tokenize(query)
    pool = max(limit * 4, 40)
    bm25 = _match_rowids(conn, "symbols_fts", tokenized, pool)
    trigram = _match_rowids(conn, "symbols_tri", query, pool) if len(query) >= 3 else []
    fused = _rrf([bm25, trigram]) if trigram else bm25

    if fused:
        placeholders = ",".join("?" for _ in fused)
        sql = (
            "SELECT rowid, kind, name, file_path, start_line, end_line, docstring "
            f"FROM symbols WHERE rowid IN ({placeholders})"
        )
        params: list = list(fused)
        if kind_filter:
            sql += " AND kind = ?"
            params.append(kind_filter)
        with _FTS_LOCK:
            rows = conn.execute(sql, params).fetchall()
        by_rowid = {row[0]: row for row in rows}
        # Preserve the fused order; assign a descending score for display.
        ordered = [by_rowid[r] for r in fused if r in by_rowid][:limit]
        for i, row in enumerate(ordered):
            results.append(
                FTSResult(
                    kind=row[1],
                    name=row[2],
                    file_path=row[3],
                    start_line=row[4],
                    end_line=row[5],
                    docstring=row[6][:200] if row[6] else "",
                    score=1.0 / (i + 1),
                )
            )

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


def fts_lookup_symbol(
    conn: sqlite3.Connection, name: str, limit: int = 50
) -> list[FTSResult]:
    """Exact-name symbol lookup, the definition-finding counterpart of
    :func:`fts_search`.

    Used when the graph DB cannot be opened (a federated child whose own
    owner holds the write lock) and a name still has to resolve to a
    definition. Scanner findings share the symbols table but are not
    definitions, so they never come back here.
    """
    with _FTS_LOCK:
        rows = conn.execute(
            "SELECT kind, name, file_path, start_line, end_line, docstring "
            "FROM symbols WHERE name = ? AND kind != 'finding' LIMIT ?",
            (name, limit),
        ).fetchall()
    return [
        FTSResult(
            kind=row[0],
            name=row[1],
            file_path=row[2],
            start_line=row[3],
            end_line=row[4],
            docstring=row[5][:200] if row[5] else "",
            score=1.0,
        )
        for row in rows
    ]


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
        # Delete the old rowid's postings before REPLACE reassigns the rowid
        # (see upsert_symbol) so the external-content index stays in sync.
        old = conn.execute(
            "SELECT rowid, title, body, kind FROM memory_entries WHERE path = ?",
            (path,),
        ).fetchone()
        if old:
            o_rowid, o_title, o_body, o_kind = old
            for tbl in ("memory_fts", "memory_tri"):
                conn.execute(
                    f"INSERT INTO {tbl}({tbl}, rowid, title, body, kind) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (o_rowid, o_title or "", o_body or "", o_kind or "other"),
                )
        conn.execute(
            "INSERT OR REPLACE INTO memory_entries(path, kind, title, body, mtime) VALUES (?, ?, ?, ?, ?)",
            (path, kind or "other", title or "", body or "", mtime),
        )
        rowid = conn.execute(
            "SELECT rowid FROM memory_entries WHERE path = ?", (path,)
        ).fetchone()
        if rowid:
            conn.execute(
                "INSERT INTO memory_fts(rowid, title, body, kind) VALUES (?, ?, ?, ?)",
                (rowid[0], title or "", body or "", kind or "other"),
            )
            conn.execute(
                "INSERT INTO memory_tri(rowid, title, body, kind) VALUES (?, ?, ?, ?)",
                (rowid[0], title or "", body or "", kind or "other"),
            )


def delete_memory_entry(conn: sqlite3.Connection, path: str) -> None:
    with _FTS_LOCK:
        row = conn.execute(
            "SELECT rowid, title, body, kind FROM memory_entries WHERE path = ?",
            (path,),
        ).fetchone()
        if row:
            rowid, title, body, kind = row
            # External-content FTS5 delete needs the exact indexed values.
            for tbl in ("memory_fts", "memory_tri"):
                conn.execute(
                    f"INSERT INTO {tbl}({tbl}, rowid, title, body, kind) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rowid, title or "", body or "", kind or "other"),
                )
        conn.execute("DELETE FROM memory_entries WHERE path = ?", (path,))


def memory_search(
    conn: sqlite3.Connection,
    query: str,
    kind: str | None = None,
    limit: int = 10,
) -> list[MemoryHit]:
    """Search memory entries, fusing tokenized BM25 with trigram
    substring matching (RRF). Returns hits ordered by relevance."""
    out: list[MemoryHit] = []
    try:
        pool = max(limit * 4, 40)
        bm25 = _match_rowids(conn, "memory_fts", _tokenize(query), pool)
        trigram = (
            _match_rowids(conn, "memory_tri", query, pool) if len(query) >= 3 else []
        )
        fused = _rrf([bm25, trigram]) if trigram else bm25
        if not fused:
            raise sqlite3.OperationalError("no fts match")
        placeholders = ",".join("?" for _ in fused)
        sql = (
            "SELECT rowid, path, kind, title, body FROM memory_entries "
            f"WHERE rowid IN ({placeholders})"
        )
        params: list = list(fused)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        with _FTS_LOCK:
            rows = conn.execute(sql, params).fetchall()
        by_rowid = {r[0]: r for r in rows}
        for r in fused:
            row = by_rowid.get(r)
            if not row:
                continue
            out.append(
                MemoryHit(
                    path=row[1],
                    kind=row[2],
                    title=row[3],
                    snippet=(row[4] or "")[:240],
                    score=1.0 / (len(out) + 1),
                )
            )
            if len(out) >= limit:
                break
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


def list_memory_entries(
    conn: sqlite3.Connection, kind: str | None = None
) -> list[MemoryHit]:
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
        MemoryHit(
            path=row[0],
            kind=row[1],
            title=row[2],
            snippet=(row[3] or "")[:240],
            score=row[4],
        )
        for row in rows
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
        # Delete the old rowid's postings before REPLACE reassigns the rowid
        # (see upsert_symbol) so the external-content index stays in sync.
        old = conn.execute(
            "SELECT rowid, title, body, slug, agent_id FROM plan_entries WHERE path = ?",
            (path,),
        ).fetchone()
        if old:
            o_rowid, o_title, o_body, o_slug, o_agent = old
            for tbl in ("plan_fts", "plan_tri"):
                conn.execute(
                    f"INSERT INTO {tbl}({tbl}, rowid, title, body, slug, agent_id) "
                    "VALUES('delete', ?, ?, ?, ?, ?)",
                    (o_rowid, o_title or "", o_body or "", o_slug or "", o_agent or ""),
                )
        conn.execute(
            "INSERT OR REPLACE INTO plan_entries(path, slug, agent_id, title, body, mtime) VALUES (?, ?, ?, ?, ?, ?)",
            (path, slug or "", agent_id or "", title or "", body or "", mtime),
        )
        rowid = conn.execute(
            "SELECT rowid FROM plan_entries WHERE path = ?", (path,)
        ).fetchone()
        if rowid:
            conn.execute(
                "INSERT INTO plan_fts(rowid, title, body, slug, agent_id) VALUES (?, ?, ?, ?, ?)",
                (rowid[0], title or "", body or "", slug or "", agent_id or ""),
            )
            conn.execute(
                "INSERT INTO plan_tri(rowid, title, body, slug, agent_id) VALUES (?, ?, ?, ?, ?)",
                (rowid[0], title or "", body or "", slug or "", agent_id or ""),
            )


def delete_plan_entry(conn: sqlite3.Connection, path: str) -> None:
    with _FTS_LOCK:
        row = conn.execute(
            "SELECT rowid, title, body, slug, agent_id FROM plan_entries WHERE path = ?",
            (path,),
        ).fetchone()
        if row:
            rowid, title, body, slug, agent_id = row
            for tbl in ("plan_fts", "plan_tri"):
                conn.execute(
                    f"INSERT INTO {tbl}({tbl}, rowid, title, body, slug, agent_id) "
                    "VALUES('delete', ?, ?, ?, ?, ?)",
                    (rowid, title or "", body or "", slug or "", agent_id or ""),
                )
        conn.execute("DELETE FROM plan_entries WHERE path = ?", (path,))


def plan_search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[PlanHit]:
    """Search plan files, fusing tokenized BM25 with trigram substring
    matching (RRF)."""
    out: list[PlanHit] = []
    try:
        pool = max(limit * 4, 40)
        bm25 = _match_rowids(conn, "plan_fts", _tokenize(query), pool)
        trigram = (
            _match_rowids(conn, "plan_tri", query, pool) if len(query) >= 3 else []
        )
        fused = _rrf([bm25, trigram]) if trigram else bm25
        if not fused:
            raise sqlite3.OperationalError("no fts match")
        placeholders = ",".join("?" for _ in fused)
        with _FTS_LOCK:
            rows = conn.execute(
                "SELECT rowid, path, slug, agent_id, title, body "
                f"FROM plan_entries WHERE rowid IN ({placeholders})",
                list(fused),
            ).fetchall()
        by_rowid = {r[0]: r for r in rows}
        for r in fused:
            row = by_rowid.get(r)
            if not row:
                continue
            out.append(
                PlanHit(
                    path=row[1],
                    slug=row[2],
                    agent_id=row[3],
                    title=row[4],
                    snippet=(row[5] or "")[:240],
                    score=1.0 / (len(out) + 1),
                )
            )
            if len(out) >= limit:
                break
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


def list_plan_entries(
    conn: sqlite3.Connection, agent_only: bool = False, limit: int = 50
) -> list[PlanHit]:
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
