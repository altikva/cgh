# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: symbols_fts must be rebuilt in the tokenized form its writers
#              use. A rebuild from raw names made the next delete leave
#              orphaned postings, and ranked queries then failed with
#              "database disk image is malformed". Covers the rebuild itself,
#              the one-time repair of stores written by an older cgh, the
#              search-side self-heal, and that a bad query stays a quiet miss.
#              CamelCase names on purpose: for a one-word name like Widget the
#              raw and tokenized forms coincide and hide the bug.

from __future__ import annotations

import sqlite3

from codegraph.core.fts import (
    delete_file_symbols,
    fts_search,
    get_fts_conn,
    rebuild_fts_indexes,
    upsert_symbol,
)


def _orphans(conn: sqlite3.Connection) -> int:
    """Rowids the word index still lists but the content table no longer has.
    FTS5's own integrity-check does not see these, so count them directly."""
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS temp.v "
        "USING fts5vocab(main, symbols_fts, 'instance')"
    )
    return conn.execute(
        "SELECT count(DISTINCT doc) FROM temp.v "
        "WHERE doc NOT IN (SELECT rowid FROM symbols)"
    ).fetchone()[0]


def _ranked(conn: sqlite3.Connection, term: str) -> list[int]:
    # ORDER BY rank is what reads each hit's row, so it is what trips on an
    # orphan; a plain MATCH would pass.
    return [
        r[0]
        for r in conn.execute(
            "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH ? ORDER BY rank",
            (term,),
        ).fetchall()
    ]


def _seed(conn: sqlite3.Connection) -> None:
    upsert_symbol(conn, "/a.py::GoTrueClient", "class", "GoTrueClient", "/a.py", 1)
    upsert_symbol(
        conn, "/a.py::parseHTTPResponse", "function", "parseHTTPResponse", "/a.py", 9
    )
    conn.commit()


def _plant_orphan(conn: sqlite3.Connection) -> None:
    """Postings for a rowid with no content row and no size entry, the shape
    found on real stores. An FTS insert writes the size entry too, and with it
    ranking would not fail, so that entry is removed to match."""
    conn.execute(
        "INSERT INTO symbols_fts(rowid, name, docstring) VALUES (9999, 'gotrue', '')"
    )
    conn.execute("DELETE FROM symbols_fts_docsize WHERE id = 9999")


def _age_to_legacy(conn: sqlite3.Connection) -> None:
    """Put a store in the state an older cgh left behind: word index rebuilt
    from raw names, one orphaned posting, and no index version."""
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    _plant_orphan(conn)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()


def test_rebuild_then_delete_leaves_no_orphans(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)

    rebuild_fts_indexes(conn)
    delete_file_symbols(conn, "/a.py")  # raised "malformed" before the fix
    conn.commit()

    assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
    assert _orphans(conn) == 0


def test_rebuild_indexes_the_tokenized_form(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)

    rebuild_fts_indexes(conn)

    # "true" exists only in the word-split form "Go True Client".
    assert len(_ranked(conn, "true")) == 1


def test_legacy_store_is_repaired_on_open(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)
    _age_to_legacy(conn)
    conn.close()

    conn = get_fts_conn(tmp_path)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert _orphans(conn) == 0
    assert len(_ranked(conn, "gotrue")) == 0  # no longer raises
    # The repaired index matches what deletes replay, so the loop is broken.
    delete_file_symbols(conn, "/a.py")
    conn.commit()
    assert _orphans(conn) == 0


def test_repair_runs_once(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)
    conn.close()

    conn = get_fts_conn(tmp_path)
    # An orphan planted after the upgrade stays put on the next open: the
    # version gate skips the rebuild instead of paying it on every open.
    _plant_orphan(conn)
    conn.commit()
    conn.close()

    conn = get_fts_conn(tmp_path)
    assert _orphans(conn) == 1


def test_ranked_search_self_heals_orphans(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)
    _plant_orphan(conn)
    conn.commit()

    results = fts_search(conn, "gotrue")  # raised "malformed" before the fix

    assert isinstance(results, list)
    assert _orphans(conn) == 0


def test_query_syntax_error_is_still_empty(tmp_path):
    conn = get_fts_conn(tmp_path)
    _seed(conn)

    # An FTS5 syntax error is an OperationalError, itself a DatabaseError: it
    # must stay a quiet miss, not be mistaken for corruption and rebuild.
    assert fts_search(conn, "/auth/callback") == []
