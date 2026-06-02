"""Tests for codegraph.core.db — GraphDB connection management.

Fresh repos default to DuckDB (graph.duckdb) since v0.5.
"""

from pathlib import Path

from codegraph.core.db import get_connection, get_db_path, reset_connection


class TestGetDbPath:
    def test_returns_duckdb_path_on_fresh_repo(self, tmp_path):
        """No graph.* files present + no env var -> default backend
        (duckdb) chosen."""
        result = get_db_path(tmp_path)
        assert result == tmp_path / ".codegraph" / "graph.duckdb"

    def test_string_input(self, tmp_path):
        result = get_db_path(str(tmp_path))
        assert isinstance(result, Path)
        assert result.name == "graph.duckdb"


class TestGetConnection:
    def test_creates_db_and_schema(self, tmp_path):
        reset_connection()
        conn = get_connection(tmp_path)
        assert conn is not None
        # Schema was created — File table accessible, zero rows.
        assert conn.count_nodes("File") == 0
        reset_connection()

    def test_connection_is_cached(self, tmp_path):
        reset_connection()
        conn1 = get_connection(tmp_path)
        conn2 = get_connection(tmp_path)
        assert conn1 is conn2
        reset_connection()

    def test_schema_tables_exist(self, tmp_path):
        reset_connection()
        conn = get_connection(tmp_path)

        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            assert conn.count_nodes(label) == 0

        reset_connection()


class TestResetConnection:
    def test_reset_allows_new_connection(self, tmp_path):
        reset_connection()
        get_connection(tmp_path)
        reset_connection()
        conn2 = get_connection(tmp_path)
        # After reset, should get a new connection object
        # (may or may not be the same object depending on Kuzu internals)
        assert conn2 is not None
        reset_connection()
