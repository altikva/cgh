"""
Parity tests for the GraphDB write helpers (upsert_node, ensure_edge,
purge_file_data) across both backends.

Each test runs against Kuzu and DuckDB and asserts identical observable
state. This locks in the contract while the indexer is being ported in
this PR — once the indexer uses only these helpers, this test pinning
keeps the two backends in lockstep.
"""

from __future__ import annotations

import pytest

from codegraph.core.db_duckdb import DuckDBGraphDB
from codegraph.core.db_kuzu import KuzuGraphDB

# ---- per-backend factories -------------------------------------------------


@pytest.fixture
def kuzu_db(tmp_path):
    import kuzu

    from codegraph.core.schema import init_schema

    db = kuzu.Database(str(tmp_path / "graph.db"))
    raw = kuzu.Connection(db)
    init_schema(raw)
    yield KuzuGraphDB(raw)
    raw.close()
    db.close()


@pytest.fixture
def duckdb_db(tmp_path):
    db = DuckDBGraphDB(str(tmp_path / "graph.duckdb"))
    yield db
    db.close()


# Each test will be parametrized over the two fixtures. Pytest does this
# via the indirect fixture trick:
@pytest.fixture(params=["kuzu_db", "duckdb_db"])
def graphdb(request):
    return request.getfixturevalue(request.param)


# ---- file_count helper -----------------------------------------------------


def _count(db, table_or_label_kuzu: str, table_duckdb: str) -> int:
    """Backend-aware row count. Kuzu uses MATCH, DuckDB uses SELECT."""
    if isinstance(db, KuzuGraphDB):
        result = db.execute(f"MATCH (n:{table_or_label_kuzu}) RETURN count(n) AS c")
    else:
        result = db.execute(f"SELECT count(*) AS c FROM {table_duckdb}")
    return int(result.get_next()[0])


def _edge_count(db, edge_type_kuzu: str, table_duckdb: str) -> int:
    if isinstance(db, KuzuGraphDB):
        result = db.execute(f"MATCH ()-[r:{edge_type_kuzu}]->() RETURN count(r) AS c")
    else:
        result = db.execute(f"SELECT count(*) AS c FROM {table_duckdb}")
    return int(result.get_next()[0])


# ---- tests -----------------------------------------------------------------


class TestUpsertNode:
    def test_creates_file_node(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python", "mtime": 1.0})
        assert _count(graphdb, "File", "file") == 1

    def test_idempotent_upsert(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        assert _count(graphdb, "File", "file") == 1

    def test_upsert_updates_props(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python", "mtime": 1.0})
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "typescript", "mtime": 2.0})
        # Query the lang field back.
        if isinstance(graphdb, KuzuGraphDB):
            r = graphdb.execute("MATCH (f:File {path: '/a.py'}) RETURN f.lang AS l, f.mtime AS m")
        else:
            r = graphdb.execute("SELECT lang AS l, mtime AS m FROM file WHERE path = '/a.py'")
        row = r.get_next()
        assert row[0] == "typescript"
        assert row[1] == 2.0

    def test_unknown_label_raises(self, graphdb):
        with pytest.raises(ValueError):
            graphdb.upsert_node("BogusLabel", "id", "x", {})


class TestEnsureEdge:
    def test_creates_calls_edge(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py"})
        graphdb.upsert_node("Function", "id", "a::bar", {"name": "bar", "file_path": "/a.py"})
        graphdb.ensure_edge("CALLS", "a::foo", "a::bar")
        assert _edge_count(graphdb, "CALLS", "edge_calls") == 1

    def test_idempotent_edge(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py"})
        graphdb.upsert_node("Function", "id", "a::bar", {"name": "bar", "file_path": "/a.py"})
        graphdb.ensure_edge("CALLS", "a::foo", "a::bar")
        graphdb.ensure_edge("CALLS", "a::foo", "a::bar")
        assert _edge_count(graphdb, "CALLS", "edge_calls") == 1

    def test_imports_edge_with_symbol(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        graphdb.upsert_node("File", "path", "/b.py", {"lang": "python"})
        graphdb.ensure_edge("IMPORTS", "/a.py", "/b.py", {"symbol": "helper"})
        assert _edge_count(graphdb, "IMPORTS", "edge_imports") == 1

    def test_unknown_edge_raises(self, graphdb):
        with pytest.raises(ValueError):
            graphdb.ensure_edge("BOGUS", "x", "y")


class TestPurgeFileData:
    def test_purge_drops_functions_and_edges(self, graphdb):
        # Set up a file with one function + a CALLS self-edge
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        graphdb.upsert_node(
            "Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py"}
        )
        graphdb.upsert_node(
            "Function", "id", "a::bar", {"name": "bar", "file_path": "/a.py"}
        )
        graphdb.ensure_edge("CALLS", "a::foo", "a::bar")

        graphdb.purge_file_data("/a.py")

        # Functions gone, the CALLS edge gone with them
        assert _count(graphdb, "Function", "function") == 0
        assert _edge_count(graphdb, "CALLS", "edge_calls") == 0
        # File node stays — the indexer re-upserts it
        assert _count(graphdb, "File", "file") == 1

    def test_purge_drops_imports_outgoing(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        graphdb.upsert_node("File", "path", "/b.py", {"lang": "python"})
        graphdb.ensure_edge("IMPORTS", "/a.py", "/b.py", {"symbol": "x"})

        graphdb.purge_file_data("/a.py")
        assert _edge_count(graphdb, "IMPORTS", "edge_imports") == 0

    def test_purge_other_files_untouched(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {"lang": "python"})
        graphdb.upsert_node(
            "Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py"}
        )
        graphdb.upsert_node("File", "path", "/b.py", {"lang": "python"})
        graphdb.upsert_node(
            "Function", "id", "b::bar", {"name": "bar", "file_path": "/b.py"}
        )

        graphdb.purge_file_data("/a.py")
        # b.py's data intact
        assert _count(graphdb, "Function", "function") == 1
