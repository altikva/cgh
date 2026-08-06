# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: External-content FTS index integrity. Re-indexing a symbol
#              must not orphan the old rowid's trigram postings (which used
#              to corrupt the index into "database disk image is malformed"),
#              and a already-corrupt index must self-heal on reindex instead
#              of crashing cgh init.

from __future__ import annotations

import sqlite3

from codegraph.core.fts import (
    delete_file_symbols,
    get_fts_conn,
    rebuild_fts_indexes,
    upsert_symbol,
)


def _integrity_ok(conn: sqlite3.Connection, table: str) -> bool:
    """FTS5's own consistency probe: raises if the index disagrees with
    its content table."""
    try:
        conn.execute(f"INSERT INTO {table}({table}) VALUES('integrity-check')")
        return True
    except sqlite3.DatabaseError:
        return False


def test_reindex_same_symbol_leaves_no_orphan(tmp_path):
    """Upserting the same sym_id with a changed name must drop the old
    trigram postings, not accumulate them (the corruption root cause)."""
    conn = get_fts_conn(tmp_path)
    upsert_symbol(conn, "s1", "function", "AlphaHandler", "/a.py", 1)
    conn.commit()
    upsert_symbol(conn, "s1", "function", "BetaHandler", "/a.py", 1)  # re-index
    conn.commit()

    assert _integrity_ok(conn, "symbols_tri")
    assert _integrity_ok(conn, "symbols_fts")
    # exactly one live row, and the old name's fragment no longer matches
    n = conn.execute("SELECT count(*) FROM symbols WHERE sym_id='s1'").fetchone()[0]
    assert n == 1
    hits = conn.execute(
        "SELECT count(*) FROM symbols_tri WHERE symbols_tri MATCH ?", ("alph",)
    ).fetchone()[0]
    assert hits == 0  # "AlphaHandler" fully removed
    hits = conn.execute(
        "SELECT count(*) FROM symbols_tri WHERE symbols_tri MATCH ?", ("beta",)
    ).fetchone()[0]
    assert hits == 1


def test_delete_after_reindex_does_not_raise(tmp_path):
    conn = get_fts_conn(tmp_path)
    upsert_symbol(conn, "s1", "function", "AlphaHandler", "/a.py", 1)
    upsert_symbol(conn, "s1", "function", "BetaHandler", "/a.py", 1)
    conn.commit()
    delete_file_symbols(conn, "/a.py")  # must not raise "malformed"
    conn.commit()
    assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
    assert _integrity_ok(conn, "symbols_tri")


def test_rebuild_reindexes_from_content(tmp_path):
    """rebuild_fts_indexes drops and repopulates every index from its
    content table, leaving it consistent and queryable."""
    conn = get_fts_conn(tmp_path)
    upsert_symbol(conn, "s1", "function", "Widget", "/a.py", 1)
    upsert_symbol(conn, "s2", "function", "Gadget", "/b.py", 1)
    conn.commit()

    rebuild_fts_indexes(conn)
    assert _integrity_ok(conn, "symbols_tri")
    assert _integrity_ok(conn, "symbols_fts")
    # 'dget' sits inside both Wi-dget and Ga-dget: the rebuilt trigram index
    # still resolves the fragment back to both symbols.
    names = {
        r[0]
        for r in conn.execute(
            "SELECT s.name FROM symbols_tri t JOIN symbols s ON s.rowid = t.rowid "
            "WHERE symbols_tri MATCH ?",
            ("dget",),
        ).fetchall()
    }
    assert names == {"Widget", "Gadget"}


class _RaiseOnceConn:
    """Forwards to a real connection but raises a malformed error the first
    time a symbols_tri 'delete' runs, to exercise the self-heal retry."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self._raised = False

    def execute(self, sql, *args):
        if not self._raised and "symbols_tri" in sql and "'delete'" in sql:
            self._raised = True
            raise sqlite3.DatabaseError("database disk image is malformed")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_delete_file_symbols_self_heals_on_malformed(tmp_path):
    conn = get_fts_conn(tmp_path)
    upsert_symbol(conn, "s1", "function", "Widget", "/a.py", 1)
    conn.commit()
    flaky = _RaiseOnceConn(conn)
    # First 'delete' raises malformed; delete_file_symbols must rebuild and
    # retry rather than propagate the crash.
    delete_file_symbols(flaky, "/a.py")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
    assert _integrity_ok(conn, "symbols_tri")
