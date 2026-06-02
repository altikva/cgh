"""
Regression: opening a RW connection after a RO one was cached must not
crash. DuckDB rejects a same-file RO + RW pair inside the same process
("Can't open a connection to same database file with a different
configuration than existing connections"). `cgh init` hits this on a
repo that already has a graph DB: the existing-state probe opens RO,
then index_repo asks for RW and blows up.

`get_connection` now closes any cached RO conn before opening RW; the
test below mirrors that exact sequence on the DuckDB backend and just
asserts it doesn't raise.
"""

from __future__ import annotations

import pytest

from codegraph.core.db import (
    get_connection,
    get_readonly_connection,
    reset_connection,
)


@pytest.fixture
def duckdb_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CGH_DB", "duckdb")
    reset_connection()
    # Seed the file so the RO open in step 1 has something to point at.
    get_connection(tmp_path)
    reset_connection()
    yield tmp_path
    reset_connection()


def test_rw_after_ro_does_not_raise(duckdb_repo):
    ro = get_readonly_connection(duckdb_repo)
    assert ro is not None

    # Used to raise _duckdb.ConnectionException here.
    rw = get_connection(duckdb_repo)
    assert rw is not None
    # RW conn is a real, usable write connection.
    assert rw.count_nodes("File") == 0


def test_ro_after_rw_reuses_the_rw_conn(duckdb_repo):
    rw = get_connection(duckdb_repo)
    ro = get_readonly_connection(duckdb_repo)
    # No second physical conn — same object handed back.
    assert ro is rw
