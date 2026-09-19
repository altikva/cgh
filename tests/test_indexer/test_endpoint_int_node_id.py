"""
Tests for codegraph.indexer._ingest_endpoints proving it survives a
non-string node id (an int) from find_node_keys without crashing,
coercing it instead.
"""

from __future__ import annotations

from codegraph.indexer import _ingest_endpoints

# ---- test doubles ----------------------------------------------------------


class FakeConn:
    """Mock connection for testing endpoint ingestion."""

    def __init__(self, keys):
        self.keys = keys
        self.edges = []

    def upsert_node(self, *args, **kwargs):
        pass

    def ensure_edge(self, rel, src, dst):
        self.edges.append((rel, src, dst))

    def find_node_keys(self, label, field, value):
        return self.keys


# ---- tests -----------------------------------------------------------------


def test_int_node_id_is_coerced_and_skipped(tmp_path):
    """Verify _ingest_endpoints coerces int node ids instead of crashing."""
    p = tmp_path / "routes.py"
    p.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        '@app.get("/items")\n'
        "def read_items():\n"
        "    return []\n"
    )

    matching = f"{p}::read_items"
    conn = FakeConn([12345, matching, "other/x.py::read_items"])

    n = _ingest_endpoints(conn, p)

    assert n == 1
    assert ("IMPLEMENTED_BY", f"{p}::GET::/items", matching) in conn.edges

    # Verify no edge has 12345 or '12345' as dst (third item)
    dsts = {dst for rel, src, dst in conn.edges}
    assert 12345 not in dsts
    assert "12345" not in dsts

    # Verify no edge has 'other/x.py::read_items' as dst
    assert "other/x.py::read_items" not in dsts
