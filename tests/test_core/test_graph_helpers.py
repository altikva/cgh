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

# Kuzu is an optional extra since v0.4.2. The parity tests in this file
# require both backends to be importable; skip the whole module when
# kuzu isn't available so DuckDB-only installs (e.g. Python 3.14) get
# a clean run.
pytest.importorskip("kuzu")

from codegraph.core.db_duckdb import DuckDBGraphDB  # noqa: E402
from codegraph.core.db_kuzu import KuzuGraphDB  # noqa: E402

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


class TestFindNodes:
    def test_exact_where(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py"})
        graphdb.upsert_node("Function", "id", "a::bar", {"name": "bar", "file_path": "/a.py"})
        rows = graphdb.find_nodes(
            "Function", where={"name": "foo"}, return_fields=["id", "name"]
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "foo"

    def test_contains_substring(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::handle_x", {"name": "handle_x", "file_path": "/a.py"})
        graphdb.upsert_node("Function", "id", "a::handle_y", {"name": "handle_y", "file_path": "/a.py"})
        graphdb.upsert_node("Function", "id", "a::other", {"name": "other", "file_path": "/a.py"})
        rows = graphdb.find_nodes(
            "Function", contains={"name": "handle"}, return_fields=["name"]
        )
        names = {r["name"] for r in rows}
        assert names == {"handle_x", "handle_y"}

    def test_contains_multi_field_or(self, graphdb):
        graphdb.upsert_node(
            "TFResource", "id", "r1",
            {"name": "bucket", "type": "aws_s3_bucket", "file_path": "/a.tf"},
        )
        graphdb.upsert_node(
            "TFResource", "id", "r2",
            {"name": "queue", "type": "aws_sqs_queue", "file_path": "/a.tf"},
        )
        rows = graphdb.find_nodes(
            "TFResource",
            contains={"name": "bucket", "type": "bucket"},
            return_fields=["id"],
        )
        assert len(rows) == 1
        assert rows[0]["id"] == "r1"

    def test_limit(self, graphdb):
        for i in range(5):
            graphdb.upsert_node(
                "Function", "id", f"a::f{i}", {"name": f"f{i}", "file_path": "/a.py"}
            )
        rows = graphdb.find_nodes("Function", limit=2, return_fields=["id"])
        assert len(rows) == 2


class TestFindNodesOrderBy:
    def test_order_by_field(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::z", {"name": "z", "file_path": "/a.py", "start_line": 30})
        graphdb.upsert_node("Function", "id", "a::a", {"name": "a", "file_path": "/a.py", "start_line": 10})
        graphdb.upsert_node("Function", "id", "a::m", {"name": "m", "file_path": "/a.py", "start_line": 20})
        rows = graphdb.find_nodes(
            "Function", return_fields=["name"], order_by=["start_line"]
        )
        assert [r["name"] for r in rows] == ["a", "m", "z"]


class TestFindNodesWithoutIncoming:
    def test_function_with_no_callers_returned(self, graphdb):
        # foo() is never called
        graphdb.upsert_node("Function", "id", "a::foo", {"name": "foo", "file_path": "/a.py", "start_line": 1, "end_line": 2})
        # bar() is called by baz()
        graphdb.upsert_node("Function", "id", "a::bar", {"name": "bar", "file_path": "/a.py", "start_line": 4, "end_line": 5})
        graphdb.upsert_node("Function", "id", "a::baz", {"name": "baz", "file_path": "/a.py", "start_line": 8, "end_line": 9})
        graphdb.ensure_edge("CALLS", "a::baz", "a::bar")

        rows = graphdb.find_nodes_without_incoming(
            "Function", "CALLS", return_fields=["name"]
        )
        names = {r["name"] for r in rows}
        assert "foo" in names
        # baz is never called either; foo + baz expected. bar has caller -> excluded.
        assert "baz" in names
        assert "bar" not in names

    def test_exclude_underscore_prefix(self, graphdb):
        graphdb.upsert_node("Function", "id", "a::_priv", {"name": "_priv", "file_path": "/a.py", "start_line": 1, "end_line": 2})
        graphdb.upsert_node("Function", "id", "a::pub", {"name": "pub", "file_path": "/a.py", "start_line": 4, "end_line": 5})
        rows = graphdb.find_nodes_without_incoming(
            "Function", "CALLS", exclude_name_prefix="_", return_fields=["name"]
        )
        names = {r["name"] for r in rows}
        assert "pub" in names
        assert "_priv" not in names


class TestFindNeighbors:
    def setup_data(self, db):
        db.upsert_node("Function", "id", "a::caller", {"name": "caller", "file_path": "/a.py"})
        db.upsert_node("Function", "id", "a::callee", {"name": "callee", "file_path": "/a.py"})
        db.upsert_node("Function", "id", "a::other", {"name": "callee", "file_path": "/b.py"})
        db.ensure_edge("CALLS", "a::caller", "a::callee")

    def test_anchor_on_src(self, graphdb):
        self.setup_data(graphdb)
        rows = graphdb.find_neighbors(
            "CALLS",
            src_key="a::caller",
            return_dst=["id", "name"],
        )
        assert len(rows) == 1
        assert rows[0]["dst_name"] == "callee"

    def test_anchor_on_dst_via_where(self, graphdb):
        # find_callers pattern: anchor by callee name, return caller info.
        self.setup_data(graphdb)
        rows = graphdb.find_neighbors(
            "CALLS",
            dst_where={"name": "callee"},
            return_src=["id", "file_path"],
        )
        # Two callees with name 'callee' exist (in /a.py and /b.py); only
        # the first one has a CALLS edge incoming, so we expect 1 row.
        assert len(rows) == 1
        assert rows[0]["src_id"] == "a::caller"

    def test_imports_edge_with_props(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {})
        graphdb.upsert_node("File", "path", "/b.py", {})
        graphdb.ensure_edge("IMPORTS", "/a.py", "/b.py", {"symbol": "helper"})

        rows = graphdb.find_neighbors(
            "IMPORTS",
            src_key="/a.py",
            return_dst=["path"],
            return_edge=["symbol"],
        )
        assert len(rows) == 1
        assert rows[0]["dst_path"] == "/b.py"
        assert rows[0]["edge_symbol"] == "helper"


class TestReachViaEdge:
    def test_single_hop(self, graphdb):
        graphdb.upsert_node("File", "path", "/a.py", {})
        graphdb.upsert_node("File", "path", "/b.py", {})
        graphdb.ensure_edge("IMPORTS", "/a.py", "/b.py", {"symbol": "x"})
        rows = graphdb.reach_via_edge("IMPORTS", "/a.py", max_depth=1, return_fields=["path"])
        assert len(rows) == 1
        assert rows[0]["path"] == "/b.py"

    def test_two_hop(self, graphdb):
        for p in ("/a.py", "/b.py", "/c.py"):
            graphdb.upsert_node("File", "path", p, {})
        graphdb.ensure_edge("IMPORTS", "/a.py", "/b.py", {"symbol": ""})
        graphdb.ensure_edge("IMPORTS", "/b.py", "/c.py", {"symbol": ""})

        deep = graphdb.reach_via_edge("IMPORTS", "/a.py", max_depth=2, return_fields=["path"])
        paths = {r["path"] for r in deep}
        assert paths == {"/b.py", "/c.py"}

        shallow = graphdb.reach_via_edge("IMPORTS", "/a.py", max_depth=1, return_fields=["path"])
        assert {r["path"] for r in shallow} == {"/b.py"}


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
