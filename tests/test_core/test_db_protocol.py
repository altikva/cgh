"""
Tests for the backend-neutral GraphDB / QueryResult protocol.

The protocols exist so that future backends (DuckDB, ...) can slot in
without changing call sites. This test pins the contract: the current
Kuzu adapter must satisfy GraphDB structurally, and the cached
connections returned by core.db must be GraphDB instances.
"""

from __future__ import annotations

import pytest

# Kuzu is an optional extra since v0.4.2 — skip the whole module if it
# isn't installed. The DuckDB-side protocol coverage in
# tests/test_core/test_db_duckdb.py runs unconditionally.
pytest.importorskip("kuzu")

from codegraph.core.db import get_connection, get_readonly_connection, reset_connection  # noqa: E402
from codegraph.core.db_kuzu import KuzuGraphDB, KuzuQueryResult  # noqa: E402
from codegraph.core.protocol import GraphDB, QueryResult  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    # These tests target the Kuzu adapter specifically (isinstance checks
    # against KuzuGraphDB, .raw exposing kuzu.Connection, Cypher-flavoured
    # queries like 'RETURN $n'). The DuckDB-side protocol coverage lives
    # in tests/test_core/test_db_duckdb.py.
    monkeypatch.setenv("CGH_DB", "kuzu")
    reset_connection()
    yield
    reset_connection()


class TestProtocolConformance:
    def test_kuzu_adapter_is_graphdb(self, tmp_path):
        conn = get_connection(tmp_path)
        assert isinstance(conn, GraphDB), (
            "GraphDB protocol must be runtime-checkable and KuzuGraphDB must satisfy it"
        )
        assert isinstance(conn, KuzuGraphDB), (
            "Today's cached connection must be the KuzuGraphDB adapter"
        )

    def test_kuzu_query_result_is_queryresult(self, tmp_path):
        conn = get_connection(tmp_path)
        result = conn.execute("RETURN 1 AS x")
        assert isinstance(result, QueryResult), (
            "QueryResult protocol must be runtime-checkable and KuzuQueryResult must satisfy it"
        )
        assert isinstance(result, KuzuQueryResult)

    def test_readonly_connection_is_graphdb(self, tmp_path):
        # First create the DB so a readonly open can succeed
        get_connection(tmp_path)
        reset_connection()
        ro = get_readonly_connection(tmp_path)
        assert ro is not None, "readonly open should succeed after the DB exists"
        assert isinstance(ro, GraphDB)


class TestProtocolMethods:
    def test_execute_returns_result(self, tmp_path):
        conn = get_connection(tmp_path)
        result = conn.execute("RETURN 1 AS x")
        assert result.has_next()
        row = result.get_next()
        assert row[0] == 1
        cols = result.get_column_names()
        assert cols == ["x"]

    def test_execute_with_params(self, tmp_path):
        conn = get_connection(tmp_path)
        result = conn.execute("RETURN $n AS x", {"n": 42})
        assert result.has_next()
        row = result.get_next()
        assert row[0] == 42

    def test_close_is_callable(self, tmp_path):
        conn = get_connection(tmp_path)
        # Calling close() on the adapter shouldn't raise; the actual
        # connection lifecycle is governed by reset_connection().
        # We don't assert anything beyond "no exception" because
        # closing an in-use cached connection would break subsequent
        # tests within the fixture window.
        assert callable(conn.close)


class TestRawEscapeHatch:
    """KuzuGraphDB.raw is intentionally exposed for Kuzu-specific helpers
    (federation, ...) until DuckDB lands. The DuckDB swap PR will remove
    this property — tests downstream depending on it will need updates."""

    def test_raw_exposes_kuzu_connection(self, tmp_path):
        import kuzu

        conn = get_connection(tmp_path)
        assert isinstance(conn.raw, kuzu.Connection)
