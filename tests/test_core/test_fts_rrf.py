# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: fts_search fuses the tokenized BM25 ranking with a trigram
#              substring ranking (RRF): whole-word queries still work,
#              and a fragment inside an identifier now matches where the
#              word tokenizer emitted nothing. The RRF helper is unit
#              tested on its own.

from __future__ import annotations

from pathlib import Path

from codegraph.core.fts import _rrf, fts_search, get_fts_conn, upsert_symbol


def test_rrf_rewards_agreement_and_top_rank():
    # id 2 is top of one list and present in the other: it must win.
    order = _rrf([[1, 2, 3], [2, 4]])
    assert order[0] == 2
    assert set(order) == {1, 2, 3, 4}


def _seed(tmp_path: Path):
    conn = get_fts_conn(tmp_path)
    syms = [
        ("s1", "function", "DonationHandler", "a.py", 1),
        ("s2", "function", "parse_python", "b.py", 2),
        ("s3", "class", "ReceiptManager", "c.py", 3),
    ]
    for sid, kind, name, path, line in syms:
        upsert_symbol(conn, sid, kind, name, path, line, docstring="")
    conn.commit()
    return conn


def test_whole_word_query_still_works(tmp_path):
    conn = _seed(tmp_path)
    names = [r.name for r in fts_search(conn, "handler")]
    assert "DonationHandler" in names


def test_fragment_inside_identifier_now_matches(tmp_path):
    """The word tokenizer never emits 'andl'; trigram + RRF finds it."""
    conn = _seed(tmp_path)
    names = [r.name for r in fts_search(conn, "andl")]
    assert "DonationHandler" in names


def test_snake_case_still_found_by_word(tmp_path):
    conn = _seed(tmp_path)
    names = [r.name for r in fts_search(conn, "python")]
    assert "parse_python" in names


def test_deleted_symbol_leaves_the_trigram_index(tmp_path):
    from codegraph.core.fts import delete_file_symbols

    conn = _seed(tmp_path)
    delete_file_symbols(conn, "a.py")
    conn.commit()
    names = [r.name for r in fts_search(conn, "andl")]
    assert "DonationHandler" not in names


def _seed_mp(tmp_path):
    from codegraph.core.fts import get_fts_conn, upsert_memory_entry, upsert_plan_entry

    conn = get_fts_conn(tmp_path)
    upsert_memory_entry(
        conn, "m1", "feedback", "Always use CommitHandler", "no raw git", 1.0
    )
    upsert_plan_entry(
        conn, "p1", "refactor", "a1", "Refactor DonationParser", "split it", 1.0
    )
    conn.commit()
    return conn


def test_memory_search_word_and_fragment(tmp_path):
    from codegraph.core.fts import memory_search

    conn = _seed_mp(tmp_path)
    assert [h.title for h in memory_search(conn, "commit")] == [
        "Always use CommitHandler"
    ]
    # 'mmith' sits inside comMITHandler: the word tokenizer misses it, trigram finds it.
    assert [h.title for h in memory_search(conn, "mmith")] == [
        "Always use CommitHandler"
    ]


def test_plan_search_fragment(tmp_path):
    from codegraph.core.fts import plan_search

    conn = _seed_mp(tmp_path)
    assert [h.title for h in plan_search(conn, "ationpars")] == [
        "Refactor DonationParser"
    ]


def test_memory_delete_leaves_trigram_consistent(tmp_path):
    from codegraph.core.fts import delete_memory_entry, memory_search

    conn = _seed_mp(tmp_path)
    delete_memory_entry(conn, "m1")
    conn.commit()
    assert memory_search(conn, "mmith") == []
