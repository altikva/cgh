"""Tests for codegraph.core.db — Kuzu connection management."""

from pathlib import Path

from codegraph.core.db import get_connection, get_db_path, reset_connection


class TestGetDbPath:
    def test_returns_correct_path(self, tmp_path):
        result = get_db_path(tmp_path)
        assert result == tmp_path / ".codegraph" / "graph.db"

    def test_string_input(self, tmp_path):
        result = get_db_path(str(tmp_path))
        assert isinstance(result, Path)
        assert result.name == "graph.db"


class TestGetConnection:
    def test_creates_db_and_schema(self, tmp_path):
        reset_connection()
        conn = get_connection(tmp_path)
        assert conn is not None

        # Verify schema was created — File table should exist
        result = conn.execute("MATCH (f:File) RETURN count(f) AS cnt")
        row = result.get_next()
        assert row[0] == 0  # empty but table exists

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

        # Check all node tables
        for table in ["File", "Function", "Class", "TFResource", "TFVar", "MdSection"]:
            query = "MATCH (n:" + table + ") RETURN count(n)"
            result = conn.execute(query)
            assert result.get_next()[0] == 0

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
