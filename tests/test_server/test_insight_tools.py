# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
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
from codegraph.server._budget import FILE_SUMMARY_MAX_BYTES
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


def test_file_summary_of_a_huge_file_stays_under_the_cap(insight_tools):
    """Hundreds of documented functions: the answer is cut to about 2 KB,
    stays valid JSON, and says it was cut, how many symbols there are and
    where the ones left out start."""
    root = insight_tools
    src = root / "huge.py"
    src.write_text(
        "".join(
            f'def function_number_{i}():\n    """{"Doc " * 40}"""\n    return {i}\n\n'
            for i in range(180)
        ),
        encoding="utf-8",
    )
    index_file(src, root)

    raw = _register("insight")["file_summary"](str(src))
    assert len(raw.encode()) <= FILE_SUMMARY_MAX_BYTES
    out = json.loads(raw)
    assert out["truncated"] is True and out["dropped"] > 0
    assert out["symbols_total"] == 180
    assert out["dropped"] == 180 - len(out["functions"])
    first_cut = 4 * len(out["functions"]) + 1
    assert f"start at line {first_cut} or later" in out["hint"]
    assert "symbol_lookup" in out["hint"]
    # Docstrings go before any symbol: the ones shown keep no doc.
    assert all("doc" not in f for f in out["functions"])
    assert out["functions"][0]["name"] == "function_number_0"
    # No federated child: rows carry no scope tag.
    assert "scope" not in out["functions"][0]


def test_search_symbols_limit_counts_all_kinds(insight_tools):
    """`limit` bounds the whole answer, an exact name ranked first, and the
    rest is reported as truncated."""
    root = insight_tools
    body = "".join(f"def widget_{i}():\n    return {i}\n\n" for i in range(30))
    body += "".join(f"class Widget{i}:\n    pass\n\n" for i in range(30))
    body += "def widget():\n    return 0\n"
    (root / "many.py").write_text(body, encoding="utf-8")
    index_file(root / "many.py", root)

    out = json.loads(_register("query")["search_symbols"]("widget", limit=10))
    assert len(out["results"]) == 10
    assert out["results"][0]["name"] == "widget"
    assert out["total"] > 10 and out["truncated"] is True
    assert "hint" in out
    assert "scope" not in out["results"][0]


def test_search_symbols_answer_is_capped(insight_tools):
    root = insight_tools
    body = "".join(
        f"def widget_with_a_rather_long_name_number_{i}():\n    return {i}\n\n"
        for i in range(300)
    )
    (root / "lots.py").write_text(body, encoding="utf-8")
    index_file(root / "lots.py", root)

    raw = _register("query")["search_symbols"]("widget", limit=300)
    assert len(raw.encode()) <= 10_000
    out = json.loads(raw)
    assert out["truncated"] is True and out["dropped"] > 0


def test_file_summary_clips_docstrings_before_cutting_symbols(insight_tools):
    """A file whose docstrings alone push it past the ceiling keeps every
    symbol with its line range; only the docstrings shrink."""
    root = insight_tools
    src = root / "documented.py"
    src.write_text(
        "".join(
            f'def fn_{i}():\n    """{"Explains a lot. " * 12}"""\n    return {i}\n\n'
            for i in range(20)
        ),
        encoding="utf-8",
    )
    index_file(src, root)

    raw = _register("insight")["file_summary"](str(src))
    assert len(raw.encode()) <= FILE_SUMMARY_MAX_BYTES
    out = json.loads(raw)
    assert [f["name"] for f in out["functions"]] == [f"fn_{i}" for i in range(20)]
    assert all(f["lines"] for f in out["functions"])
    assert all(len(f.get("doc", "")) <= 60 for f in out["functions"])
    assert "symbols_total" not in out


def test_file_summary_without_docstrings_carries_no_doc_key(insight_tools):
    root = insight_tools
    src = root / "bare.py"
    src.write_text("def bare():\n    return 1\n", encoding="utf-8")
    index_file(src, root)

    out = json.loads(_register("insight")["file_summary"](str(src)))
    assert out["functions"] == [{"name": "bare", "lines": "1-2"}]
