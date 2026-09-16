# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The interactive `cgh graph` view: the per-view payload (files
#              and imports, functions and calls, Terraform resources, section
#              trees), the self-contained HTML around it, the CLI's explore
#              scope, and the MCP scope the CLI falls back to while an owner
#              holds the write lock.

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.cli.commands_graph import SCOPES, cmd_graph
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server.tools_viz import register as register_viz
from codegraph.viz import generate_graph_view_html
from codegraph.viz.graphdata import build_graph_payload

VIEW_JS = Path(__file__).resolve().parents[2] / "codegraph" / "viz" / "graph_view.js"


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def indexed_repo(tmp_path):
    """A repo with all four kinds of structure: imports, calls, Terraform, docs."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "infra").mkdir()
    _git(root, "init")
    (root / "pkg" / "core.py").write_text(
        "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    (root / "pkg" / "app.py").write_text(
        "from pkg.core import run\n\n\ndef main():\n    return run()\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_app.py").write_text(
        "from pkg.app import main\n\n\ndef test_main():\n    assert main() == 1\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Guide\n\ntext\n\n## Usage\n\nmore\n\n### Detail\n\nleaf\n", encoding="utf-8"
    )
    (root / "infra" / "main.tf").write_text(
        'resource "google_storage_bucket" "state" {\n  name = "x"\n}\n\n'
        'variable "region" {\n  type = string\n}\n',
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    reset_connection()
    index_repo(str(root))
    reset_connection()
    yield root
    reset_connection()


def _conn(root):
    from codegraph.core.db import get_readonly_connection

    return get_readonly_connection(str(root))


def _payload(root, **kw):
    return build_graph_payload(_conn(root), str(root), **kw)


def _names(view):
    return [n["n"] for n in view["nodes"]]


def _edge_pairs(view):
    names = _names(view)
    return {(names[s], names[t]) for s, t in view["edges"]}


class TestFilesView:
    def test_carries_every_file_with_its_metadata(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["files"]

        by_path = {n["p"]: n for n in view["nodes"]}
        assert by_path["pkg/core.py"]["f"] == 2  # helper + run
        assert by_path["pkg/core.py"]["l"] == "python"
        assert by_path["pkg/core.py"]["k"] == "file"
        assert by_path["tests/test_app.py"]["r"] == "test"
        assert all(not n["p"].startswith("/") for n in view["nodes"])

    def test_import_edges_index_into_the_node_list(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["files"]

        paths = [n["p"] for n in view["nodes"]]
        pairs = {(paths[s], paths[t]) for s, t in view["edges"]}
        assert ("pkg/app.py", "pkg/core.py") in pairs
        assert all(s != t for s, t in view["edges"])


class TestSymbolsView:
    def test_is_capped_by_call_degree(self, indexed_repo):
        view = _payload(indexed_repo, max_symbols=1)["views"]["symbols"]

        assert len(view["nodes"]) <= 1
        for s, t in view["edges"]:
            assert 0 <= s < len(view["nodes"]) and 0 <= t < len(view["nodes"])

    def test_functions_carry_their_line(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["symbols"]

        assert view["nodes"], "the fixture calls helper() from run()"
        assert all(n["k"] == "function" for n in view["nodes"])
        assert any(n["s"] > 0 for n in view["nodes"])


class TestInfraView:
    def test_resources_hang_off_the_file_that_declares_them(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["infra"]

        kinds = {n["k"] for n in view["nodes"]}
        assert kinds == {"file", "resource"}
        assert "google_storage_bucket.state" in _names(view)
        assert ("main.tf", "google_storage_bucket.state") in _edge_pairs(view)

    def test_cap_keeps_the_edges_consistent(self, indexed_repo):
        view = _payload(indexed_repo, max_resources=0)["views"].get("infra")

        # nothing kept, so the view drops out of the payload entirely
        assert view is None


class TestDocsView:
    def test_sections_hang_off_their_file_and_nest(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["docs"]

        names = _names(view)
        assert "# Guide" in names
        assert "## Usage" in names
        pairs = _edge_pairs(view)
        assert ("README.md", "# Guide") in pairs
        assert ("# Guide", "## Usage") in pairs

    def test_section_level_travels_in_the_line_field(self, indexed_repo):
        view = _payload(indexed_repo)["views"]["docs"]

        by_name = {n["n"]: n for n in view["nodes"] if n["k"] == "section"}
        assert by_name["# Guide"]["s"] == 1
        assert by_name["## Usage"]["s"] == 2

    def test_shallow_headings_survive_the_cap(self, indexed_repo):
        view = _payload(indexed_repo, max_sections=1)["views"]["docs"]

        sections = [n for n in view["nodes"] if n["k"] == "section"]
        assert len(sections) == 1
        assert sections[0]["s"] == 1  # the h1, not a leaf
        assert view["truncated"] >= 1


class TestPayload:
    def test_only_views_with_nodes_are_shipped(self, indexed_repo):
        payload = _payload(indexed_repo)

        assert set(payload["views"]) == {"files", "symbols", "infra", "docs"}
        assert all(v["nodes"] for v in payload["views"].values())
        assert payload["repo"] == indexed_repo.name

    def test_every_view_declares_how_to_label_itself(self, indexed_repo):
        for view in _payload(indexed_repo)["views"].values():
            assert view["label"] and view["outTitle"] and view["inTitle"]
            assert len(view["noun"]) == 2 and len(view["edge"]) == 2

    def test_totals_count_the_whole_index(self, indexed_repo):
        totals = _payload(indexed_repo)["totals"]

        assert totals["files"] >= 5
        assert totals["functions"] >= 4
        assert totals["resources"] >= 2  # the file node and its resource


class TestHtml:
    def test_has_every_element_the_viewer_looks_up(self, indexed_repo):
        html = generate_graph_view_html(_payload(indexed_repo), str(indexed_repo))

        wanted = set(re.findall(r"pick\('([a-z0-9-]+)'\)", VIEW_JS.read_text()))
        present = set(re.findall(r'id="([a-z0-9-]+)"', html))
        assert wanted and not wanted - present

    def test_loads_nothing_from_the_network(self, indexed_repo):
        html = generate_graph_view_html(_payload(indexed_repo), str(indexed_repo))

        assert not re.search(r'src="(?:https?:)?//', html)
        assert not re.search(r'<link[^>]+href="(?:https?:)?//', html)
        assert "fetch(" not in html
        assert "CGHGraph" in html

    def test_payload_cannot_close_its_own_script_tag(self, indexed_repo):
        hostile = _payload(indexed_repo)
        hostile["repo"] = "</script><script>window.owned = 1;//"

        html = generate_graph_view_html(hostile, str(indexed_repo))
        embedded = html.split("window.__CGH_GRAPH__ = ")[1].split("</script>")[0]
        assert "</script>" not in embedded
        assert "<\\/script>" in embedded
        assert json.loads(embedded.rstrip().rstrip(";").replace("<\\/", "</"))


class TestCli:
    def test_explore_is_the_default_scope(self):
        assert SCOPES[0] == "explore"

    def test_writes_a_self_contained_file(self, indexed_repo, tmp_path):
        out = tmp_path / "graph.html"
        cmd_graph(
            argparse.Namespace(
                root=str(indexed_repo),
                scope="explore",
                symbol=None,
                file=None,
                max_nodes=40,
                max_symbols=50,
                mermaid=False,
                html=str(out),
            )
        )

        html = out.read_text(encoding="utf-8")
        assert "window.__CGH_GRAPH__" in html
        assert "pkg/core.py" in html

    def test_mermaid_flag_is_refused_on_the_interactive_scope(
        self, indexed_repo, tmp_path, capsys
    ):
        out = tmp_path / "unused.html"
        cmd_graph(
            argparse.Namespace(
                root=str(indexed_repo),
                scope="explore",
                symbol=None,
                file=None,
                max_nodes=40,
                max_symbols=50,
                mermaid=True,
                html=str(out),
            )
        )

        assert "--mermaid" in capsys.readouterr().out
        assert not out.exists()


class TestMcpScope:
    def test_explore_returns_the_payload(self, indexed_repo):
        reset_connection()
        _srv._root = str(indexed_repo)
        _srv._conn = None
        try:
            mcp = _FakeMcp()
            register_viz(mcp)
            out = json.loads(mcp.tools["visualize_graph"](scope="explore", max_nodes=5))

            assert out["scope"] == "explore" and out["format"] == "json"
            files = out["payload"]["views"]["files"]["nodes"]
            assert any(n["p"] == "pkg/core.py" for n in files)
            assert len(out["payload"]["views"]["symbols"]["nodes"]) <= 5
        finally:
            _srv._root = None
            _srv._conn = None
            reset_connection()
