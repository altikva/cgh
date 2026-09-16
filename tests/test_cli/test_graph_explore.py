# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The interactive `cgh graph` view: the whole-graph payload, the
#              self-contained HTML around it (every element id the viewer looks
#              up, no network reference, a payload that cannot close its own
#              script tag), the CLI's explore scope, and the MCP scope the CLI
#              falls back to while an owner holds the write lock.

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
    """Two python files, one importing the other, plus a doc and a test."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
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
    (root / "README.md").write_text("# Guide\n\n## Usage\n\ntext\n", encoding="utf-8")
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


class TestPayload:
    def test_carries_every_file_with_its_metadata(self, indexed_repo):
        payload = build_graph_payload(_conn(indexed_repo), str(indexed_repo))

        by_path = {f["p"]: f for f in payload["files"]}
        assert "pkg/core.py" in by_path
        assert by_path["pkg/core.py"]["f"] == 2  # helper + run
        assert by_path["pkg/core.py"]["l"] == "python"
        assert by_path["tests/test_app.py"]["r"] == "test"
        assert by_path["README.md"]["l"] == "markdown"
        # paths are relative: the payload ships in a file anyone can open
        assert all(not f["p"].startswith("/") for f in payload["files"])

    def test_import_edges_index_into_the_file_list(self, indexed_repo):
        payload = build_graph_payload(_conn(indexed_repo), str(indexed_repo))

        paths = [f["p"] for f in payload["files"]]
        edges = {(paths[s], paths[t]) for s, t in payload["imports"]}
        assert ("pkg/app.py", "pkg/core.py") in edges
        assert all(s != t for s, t in payload["imports"])

    def test_symbol_side_is_capped_by_call_degree(self, indexed_repo):
        payload = build_graph_payload(
            _conn(indexed_repo), str(indexed_repo), max_symbols=1
        )

        assert len(payload["symbols"]) <= 1
        assert payload["totals"]["symbols_shown"] == len(payload["symbols"])
        # every call index stays inside the kept set
        for s, t in payload["calls"]:
            assert 0 <= s < len(payload["symbols"])
            assert 0 <= t < len(payload["symbols"])

    def test_totals_count_the_whole_index(self, indexed_repo):
        payload = build_graph_payload(_conn(indexed_repo), str(indexed_repo))

        assert payload["totals"]["files"] == len(payload["files"])
        assert payload["totals"]["functions"] >= 4
        assert payload["repo"] == indexed_repo.name


class TestHtml:
    def test_has_every_element_the_viewer_looks_up(self, indexed_repo):
        payload = build_graph_payload(_conn(indexed_repo), str(indexed_repo))
        html = generate_graph_view_html(payload, str(indexed_repo))

        wanted = set(re.findall(r"pick\('([a-z0-9-]+)'\)", VIEW_JS.read_text()))
        present = set(re.findall(r'id="([a-z0-9-]+)"', html))
        assert wanted, "the viewer should look up element ids"
        assert not wanted - present

    def test_loads_nothing_from_the_network(self, indexed_repo):
        payload = build_graph_payload(_conn(indexed_repo), str(indexed_repo))
        html = generate_graph_view_html(payload, str(indexed_repo))

        # a link in the footer is fine; a loaded resource is not
        assert not re.search(r'src="(?:https?:)?//', html)
        assert not re.search(r'<link[^>]+href="(?:https?:)?//', html)
        assert "fetch(" not in html
        assert "CGHGraph" in html

    def test_payload_cannot_close_its_own_script_tag(self, indexed_repo):
        hostile = build_graph_payload(_conn(indexed_repo), str(indexed_repo))
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

            assert out["scope"] == "explore"
            assert out["format"] == "json"
            assert any(f["p"] == "pkg/core.py" for f in out["payload"]["files"])
            assert len(out["payload"]["symbols"]) <= 5
        finally:
            _srv._root = None
            _srv._conn = None
            reset_connection()
