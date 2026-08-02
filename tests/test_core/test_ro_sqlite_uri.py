# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: ro_sqlite_uri must build a read-only SQLite file: URI that opens
#              on every platform, including paths with spaces (which a naive
#              f"file:{path}" would break) and Windows backslash paths.

from __future__ import annotations

import sqlite3

import pytest

from codegraph.core.utils import ro_sqlite_uri


def _seed(db_path, rows=1):
    c = sqlite3.connect(db_path)
    c.execute("CREATE TABLE t (a INTEGER)")
    for _ in range(rows):
        c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()


def test_opens_existing_db(tmp_path):
    db = tmp_path / "fts.db"
    _seed(db, rows=3)
    conn = sqlite3.connect(ro_sqlite_uri(db), uri=True)
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3
    conn.close()


def test_is_read_only(tmp_path):
    db = tmp_path / "fts.db"
    _seed(db)
    conn = sqlite3.connect(ro_sqlite_uri(db), uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES (2)")
    conn.close()


def test_handles_space_in_path(tmp_path):
    # A space (and any special char) must be percent-encoded; a raw
    # f"file:{path}" would fail to open here.
    d = tmp_path / "a b dir"
    d.mkdir()
    db = d / "fts.db"
    _seed(db, rows=2)
    conn = sqlite3.connect(ro_sqlite_uri(db), uri=True)
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 2
    conn.close()


def test_uri_shape():
    uri = ro_sqlite_uri("/tmp/x.db")
    assert uri.startswith("file:")
    assert uri.endswith("?mode=ro")
