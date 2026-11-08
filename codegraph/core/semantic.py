# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Semantic search over indexed symbols. Embeds each symbol's name
#              and docstring with the configured embedder and ranks them by
#              cosine to the query embedding. PR-1 embeds the corpus on demand
#              and caches the matrix per process, keyed on the symbol table's
#              size and newest rowid, so a reindex rebuilds it; persistence and
#              hybrid fusion with BM25 come later. The symbols live in the FTS
#              SQLite database, so this is backend-agnostic.

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from codegraph.core.embedder import Embedder, get_embedder

# Cache: (mode, count, max_rowid) -> _Corpus. Keyed so any insert, delete or
# reindex (which changes the count or the newest rowid) invalidates it.
_CACHE: dict[tuple[str, int, int], _Corpus] = {}
_CACHE_LIMIT = 4


@dataclass
class _Corpus:
    sym_ids: list[str]
    names: list[str]
    files: list[str]
    lines: list[int]
    matrix: np.ndarray  # (N, dim), unit-norm rows


@dataclass
class SemanticHit:
    sym_id: str
    name: str
    file_path: str
    line: int
    score: float


def _corpus_key(conn: sqlite3.Connection, mode: str) -> tuple[str, int, int]:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM symbols"
    ).fetchone()
    return (mode, int(row[0]), int(row[1]))


def _build_corpus(conn: sqlite3.Connection, embedder: Embedder) -> _Corpus:
    rows = conn.execute(
        "SELECT sym_id, name, file_path, start_line, docstring FROM symbols "
        "ORDER BY rowid"
    ).fetchall()
    sym_ids, names, files, lines, texts = [], [], [], [], []
    for sym_id, name, file_path, start_line, docstring in rows:
        sym_ids.append(sym_id)
        names.append(name)
        files.append(file_path)
        lines.append(int(start_line or 0))
        texts.append(f"{name} {docstring or ''}".strip())
    matrix = embedder.embed(texts)
    return _Corpus(sym_ids, names, files, lines, matrix)


def _corpus_for(conn: sqlite3.Connection, embedder: Embedder) -> _Corpus:
    key = _corpus_key(conn, embedder.mode)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _build_corpus(conn, embedder)
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = cached
    return cached


def clear_cache() -> None:
    _CACHE.clear()


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 15,
    mode: str = "lexical",
    embedder: Embedder | None = None,
) -> list[SemanticHit]:
    """Rank indexed symbols by cosine similarity of their name+docstring to the
    query. `mode` selects the embedder ('lexical' default, 'neural' opt-in);
    pass an `embedder` to reuse a loaded one."""
    emb = embedder or get_embedder(mode)
    corpus = _corpus_for(conn, emb)
    if corpus.matrix.shape[0] == 0:
        return []
    qv = emb.embed([query])[0]
    scores = corpus.matrix @ qv
    top = np.argsort(-scores)[: max(0, limit)]
    return [
        SemanticHit(
            sym_id=corpus.sym_ids[i],
            name=corpus.names[i],
            file_path=corpus.files[i],
            line=corpus.lines[i],
            score=float(scores[i]),
        )
        for i in top
    ]
