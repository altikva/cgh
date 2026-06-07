# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the graph-insight MCP tools (file_summary, impact_of,
#              path_between, import_cycles). Builds a tiny indexed repo in a
#              tmp_path, registers the tools on a fake mcp that captures the
#              closures, then asserts on the parsed JSON each tool returns.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_insight import register as register_insight
from codegraph.server.tools_query import register as register_query


class _FakeMcp:
    """Minimal stand-in for FastMCP: .tool() records the decorated function
    unchanged so tests can call the tool bodies directly."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def insight_tools(tmp_path):
    """Index a tiny repo and return (tools, repo_root)."""
    reset_connection()
    _srv._root = tmp_path.resolve()
    _srv._conn = None

    yield tmp_path.resolve()

    reset_connection()
    _srv._root = None
    _srv._conn = None


def _register(kind: str) -> dict:
    m = _FakeMcp()
    if kind == "insight":
        register_insight(m)
    else:
        register_query(m)
    return m.tools


def test_file_summary_lists_functions(insight_tools):
    root = insight_tools
    src = root / "mod.py"
    src.write_text(
        'def alpha():\n    """First."""\n    return 1\n\n\n'
        'def beta():\n    """Second."""\n    return 2\n',
        encoding="utf-8",
    )
    index_file(src, root)

    tools = _register("insight")
    out = json.loads(tools["file_summary"](str(src)))

    assert out["found"] is True
    names = {f["name"] for f in out["functions"]}
    assert names == {"alpha", "beta"}
    assert out["truncated"] is False


def test_file_summary_accepts_relative_path(insight_tools):
    root = insight_tools
    src = root / "rel.py"
    src.write_text("def only():\n    return 1\n", encoding="utf-8")
    index_file(src, root)

    tools = _register("insight")
    out = json.loads(tools["file_summary"]("rel.py"))
    assert out["found"] is True
    assert {f["name"] for f in out["functions"]} == {"only"}


def test_import_cycles_finds_two_file_cycle(insight_tools):
    root = insight_tools
    (root / "a.py").write_text(
        "import b\n\ndef fa():\n    return 1\n", encoding="utf-8"
    )
    (root / "b.py").write_text(
        "import a\n\ndef fb():\n    return 2\n", encoding="utf-8"
    )
    index_file(root / "a.py", root)
    index_file(root / "b.py", root)

    tools = _register("insight")
    out = json.loads(tools["import_cycles"]())

    assert out["count"] == 1
    cycle = set(out["cycles"][0])
    assert any(c.endswith("a.py") for c in cycle)
    assert any(c.endswith("b.py") for c in cycle)


def test_impact_of_finds_importer(insight_tools):
    root = insight_tools
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "import lib\n\ndef run():\n    return 1\n", encoding="utf-8"
    )
    index_file(root / "lib.py", root)
    index_file(root / "app.py", root)

    tools = _register("insight")
    out = json.loads(tools["impact_of"](str(root / "lib.py")))

    assert out["direction"] == "importers"
    impacted = {row["node"] for row in out["impacted"]}
    assert any(p.endswith("app.py") for p in impacted)


def test_impact_of_callers_carries_note(insight_tools):
    root = insight_tools
    # Same-file chain so CALLS edges resolve (cross-file CALLS are not linked).
    (root / "chain.py").write_text(
        "def leaf():\n    return 1\n\n\n"
        "def mid():\n    return leaf()\n\n\n"
        "def top():\n    return mid()\n",
        encoding="utf-8",
    )
    index_file(root / "chain.py", root)

    tools = _register("insight")
    out = json.loads(tools["impact_of"]("leaf"))

    assert out["direction"] == "callers"
    assert "note" in out  # CALLS over-count caveat
    nodes = {row["node"] for row in out["impacted"]}
    assert any(n.endswith("::mid") for n in nodes)
    assert any(n.endswith("::top") for n in nodes)


def test_path_between_finds_two_hop_call_path(insight_tools):
    root = insight_tools
    (root / "chain.py").write_text(
        "def leaf():\n    return 1\n\n\n"
        "def mid():\n    return leaf()\n\n\n"
        "def top():\n    return mid()\n",
        encoding="utf-8",
    )
    index_file(root / "chain.py", root)

    tools = _register("insight")
    out = json.loads(tools["path_between"]("top", "leaf"))

    assert out["found"] is True
    assert out["length"] == 2
    assert out["path"][0].endswith("::top")
    assert out["path"][-1].endswith("::leaf")


def test_path_between_no_path(insight_tools):
    root = insight_tools
    (root / "iso.py").write_text(
        "def x():\n    return 1\n\n\ndef y():\n    return 2\n", encoding="utf-8"
    )
    index_file(root / "iso.py", root)

    tools = _register("insight")
    out = json.loads(tools["path_between"]("x", "y"))
    assert out["found"] is False


def test_search_symbols_role_filter(insight_tools):
    root = insight_tools
    (root / "thing.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    index_file(root / "thing.py", root)

    tools = _register("query")
    # The file's role is "other"; filtering on a non-matching role yields none.
    out = json.loads(tools["search_symbols"]("widget", role="router"))
    assert out["results"] == []

    out2 = json.loads(tools["search_symbols"]("widget", role="other"))
    assert any(r["name"] == "widget" for r in out2["results"])
