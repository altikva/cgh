# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Semantic search core: the lexical embedder is deterministic and
#              unit-norm, cosine search over indexed symbols ranks the relevant
#              one first, and an unknown embed mode is rejected.
from __future__ import annotations

import numpy as np
import pytest

from codegraph.core.embedder import get_embedder
from codegraph.core.fts import commit, get_fts_conn, upsert_symbol
from codegraph.core.semantic import clear_cache, semantic_search


def test_lexical_embedder_is_deterministic_and_unit_norm():
    e = get_embedder("lexical")
    a = e.embed(["rotate the owner log file"])
    b = e.embed(["rotate the owner log file"])
    assert np.allclose(a, b)
    assert abs(float(np.linalg.norm(a[0])) - 1.0) < 1e-5


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        get_embedder("bogus")


def test_semantic_search_ranks_the_relevant_symbol_first(tmp_path):
    conn = get_fts_conn(tmp_path)
    upsert_symbol(
        conn,
        "s1",
        "function",
        "rotate_owner_log",
        "a.py",
        10,
        20,
        "Rotate the owner log when it grows too large",
    )
    upsert_symbol(
        conn,
        "s2",
        "function",
        "fold_accents",
        "b.py",
        3,
        4,
        "Strip accents from a word",
    )
    commit(conn)
    clear_cache()
    hits = semantic_search(conn, "rotate the log file", limit=2, mode="lexical")
    assert hits
    assert hits[0].name == "rotate_owner_log"
    assert hits[0].file_path == "a.py" and hits[0].line == 10


def test_semantic_search_empty_index_returns_nothing(tmp_path):
    conn = get_fts_conn(tmp_path)
    clear_cache()
    assert semantic_search(conn, "anything", mode="lexical") == []


def test_cmd_semantic_prints_the_relevant_symbol(tmp_path, capsys):
    from types import SimpleNamespace

    from codegraph.cli.commands_query import cmd_semantic

    conn = get_fts_conn(tmp_path)
    upsert_symbol(
        conn,
        "s1",
        "function",
        "rotate_owner_log",
        "a.py",
        10,
        20,
        "Rotate the owner log when it grows too large",
    )
    commit(conn)
    clear_cache()
    cmd_semantic(
        SimpleNamespace(
            query="rotate the log file", root=str(tmp_path), limit=5, mode="lexical"
        )
    )
    out = capsys.readouterr().out
    assert "rotate_owner_log" in out


def test_corpus_cache_distinguishes_databases(tmp_path):
    # Two FTS databases with identical symbol count and newest rowid must not
    # share a cached corpus (the federation collision the db path guards).
    a = get_fts_conn(tmp_path / "a")
    b = get_fts_conn(tmp_path / "b")
    upsert_symbol(a, "x", "function", "alpha_rotate", "f.py", 1, 2, "rotate the log")
    upsert_symbol(b, "x", "function", "beta_accents", "g.py", 1, 2, "strip accents")
    commit(a)
    commit(b)
    clear_cache()
    ha = semantic_search(a, "rotate the log", limit=1, mode="lexical")
    hb = semantic_search(b, "rotate the log", limit=1, mode="lexical")
    assert ha[0].name == "alpha_rotate"
    assert hb[0].name == "beta_accents"
