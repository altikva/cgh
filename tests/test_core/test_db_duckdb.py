"""
Tests for the DuckDB backend (codegraph.core.db_duckdb + schema_duckdb).

This PR introduces the schema + adapter end-to-end. The indexer + MCP
tools don't yet emit SQL — that's the next PR in the migration chain —
so these tests exercise the backend directly rather than through cgh's
public CLI / MCP surface.
"""

from __future__ import annotations

import pytest

from codegraph.core.db_duckdb import DuckDBGraphDB, DuckDBQueryResult
from codegraph.core.protocol import GraphDB, QueryResult


@pytest.fixture
def duckdb_db(tmp_path):
    db = DuckDBGraphDB(str(tmp_path / "graph.duckdb"))
    yield db
    db.close()


class TestProtocolConformance:
    def test_duckdb_is_graphdb(self, duckdb_db):
        assert isinstance(duckdb_db, GraphDB)

    def test_duckdb_result_is_queryresult(self, duckdb_db):
        result = duckdb_db.execute("SELECT 1 AS x")
        assert isinstance(result, QueryResult)
        assert isinstance(result, DuckDBQueryResult)


class TestQueryResult:
    def test_get_column_names(self, duckdb_db):
        result = duckdb_db.execute("SELECT 1 AS x, 'a' AS y")
        assert result.get_column_names() == ["x", "y"]

    def test_iterates_rows(self, duckdb_db):
        # Populate something so we can fetch real rows
        duckdb_db.execute("INSERT INTO file(path, lang) VALUES ('a.py', 'python')")
        duckdb_db.execute("INSERT INTO file(path, lang) VALUES ('b.py', 'python')")
        result = duckdb_db.execute("SELECT path FROM file ORDER BY path")
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        assert rows == [["a.py"], ["b.py"]]

    def test_empty_result_has_no_next(self, duckdb_db):
        result = duckdb_db.execute("SELECT path FROM file WHERE path = 'nope'")
        assert not result.has_next()


class TestSchemaInit:
    def test_node_tables_created(self, duckdb_db):
        # Each node table should be queryable (returns 0 rows on a fresh DB).
        for table in ("file", "function", "class", "endpoint",
                      "tf_resource", "tf_var", "md_section"):
            result = duckdb_db.execute(f"SELECT count(*) FROM {table}")
            assert result.has_next()
            assert result.get_next() == [0]

    def test_edge_tables_created(self, duckdb_db):
        for table in ("edge_imports", "edge_calls", "edge_inherits",
                      "edge_has_method", "edge_defines_fn", "edge_defines_class"):
            result = duckdb_db.execute(f"SELECT count(*) FROM {table}")
            assert result.has_next()
            assert result.get_next() == [0]


class TestExplicitPurge:
    """DuckDB doesn't support ON DELETE CASCADE on FKs, so the indexer's
    _purge_file equivalent will issue DELETE per edge table + per node
    type. This test pins the contract that explicit purges work — the
    actual port of _purge_file lands in the next migration PR.
    """

    def test_purge_file_chain_works(self, duckdb_db):
        # Set up: a file, a function in it, an edge between them.
        duckdb_db.execute("INSERT INTO file(path) VALUES ('a.py')")
        duckdb_db.execute(
            "INSERT INTO function(id, name, file_path) VALUES ('a.py::foo', 'foo', 'a.py')"
        )
        duckdb_db.execute(
            "INSERT INTO edge_defines_fn(from_path, to_id) VALUES ('a.py', 'a.py::foo')"
        )

        # Explicit purge: edge -> node -> root, in dependency order.
        duckdb_db.execute("DELETE FROM edge_defines_fn WHERE from_path = 'a.py'")
        duckdb_db.execute("DELETE FROM function WHERE file_path = 'a.py'")
        duckdb_db.execute("DELETE FROM file WHERE path = 'a.py'")

        for table in ("edge_defines_fn", "function", "file"):
            assert duckdb_db.execute(f"SELECT count(*) FROM {table}").get_next() == [0]


class TestBackendSelection:
    """core.db.get_connection() picks the backend from the CGH_DB env var."""

    def test_default_backend_is_kuzu(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CGH_DB", raising=False)
        from codegraph.core.db import get_connection, reset_connection
        from codegraph.core.db_kuzu import KuzuGraphDB

        reset_connection()
        try:
            conn = get_connection(tmp_path)
            assert isinstance(conn, KuzuGraphDB)
        finally:
            reset_connection()

    def test_duckdb_backend_opens_duckdb(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CGH_DB", "duckdb")
        from codegraph.core.db import get_connection, reset_connection

        reset_connection()
        try:
            conn = get_connection(tmp_path)
            assert isinstance(conn, DuckDBGraphDB)
            # And the schema is initialized — tables exist.
            result = conn.execute("SELECT count(*) FROM file")
            assert result.get_next() == [0]
        finally:
            reset_connection()
            monkeypatch.delenv("CGH_DB", raising=False)
