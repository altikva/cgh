# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh export --format graphml` writes valid GraphML on both
#              graph backends: files and symbols as nodes with repo-relative
#              paths, IMPORTS / DEFINES / CALLS edges with their type, an
#              IMPORTS edge's symbol and a CALLS edge's resolution, text
#              escaped, --scope keeping only the nodes under a path.

from __future__ import annotations

import argparse
import io
import xml.etree.ElementTree as ET

import pytest

from codegraph.cli.commands_export import cmd_export
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo
from codegraph.viz.graphml import GRAPHML_NS, write_graphml

NS = {"g": GRAPHML_NS}

FILES = {
    "pkg/__init__.py": "",
    "pkg/helpers.py": (
        'def beta():\n    """Returns <two> & "more"."""\n    return 2\n'
    ),
    "pkg/b.py": "from pkg.helpers import beta\n\n\ndef run_b():\n    return beta()\n",
    "tools/c.py": "def other():\n    return 1\n",
}


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(tmp_path, request, monkeypatch):
    monkeypatch.setenv("CGH_DB", request.param)
    for rel, body in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    reset_connection()
    index_repo(str(tmp_path))
    yield tmp_path
    reset_connection()


def _parse(text: str):
    # Parsing the exporter's own output in a test, not untrusted input.
    return ET.fromstring(text.encode("utf-8"))  # noqa: S314


def _data(el, key: str):
    for d in el.findall("g:data", NS):
        if d.get("key") == key:
            return d.text
    return None


def _export(repo, scope: str = "") -> tuple[ET.Element, dict]:
    buf = io.StringIO()
    counts = write_graphml(get_connection(repo), str(repo), buf, scope=scope)
    return _parse(buf.getvalue()), counts


def test_graphml_is_valid_and_carries_nodes_and_edges(repo):
    root, counts = _export(repo)
    assert root.tag == f"{{{GRAPHML_NS}}}graphml"
    keys = {k.get("id") for k in root.findall("g:key", NS)}
    assert {"n_label", "n_kind", "e_type", "e_resolution", "e_symbol"} <= keys

    graph = root.find("g:graph", NS)
    nodes = {n.get("id"): n for n in graph.findall("g:node", NS)}
    assert "File:pkg/helpers.py" in nodes
    assert "Function:pkg/helpers.py::beta" in nodes
    # Paths are repo-relative: no absolute path of the machine leaks.
    assert not any(str(repo) in nid for nid in nodes)
    beta = nodes["Function:pkg/helpers.py::beta"]
    assert _data(beta, "n_kind") == "Function"
    assert _data(beta, "n_file") == "pkg/helpers.py"
    assert _data(beta, "n_start_line") == "1"

    edges = graph.findall("g:edge", NS)
    ids = [e.get("id") for e in edges]
    assert len(ids) == len(set(ids))
    for e in edges:
        assert e.get("source") in nodes and e.get("target") in nodes
    calls = [e for e in edges if _data(e, "e_type") == "CALLS"]
    call = next(e for e in calls if e.get("target") == "Function:pkg/helpers.py::beta")
    assert call.get("source") == "Function:pkg/b.py::run_b"
    assert _data(call, "e_resolution") in {"resolved", "name", "ambiguous"}
    imports = [e for e in edges if _data(e, "e_type") == "IMPORTS"]
    assert any(
        e.get("source") == "File:pkg/b.py" and e.get("target") == "File:pkg/helpers.py"
        for e in imports
    )
    assert counts["nodes"] == len(nodes)
    assert counts["edges"] == len(edges)


def test_scope_keeps_only_nodes_under_the_path(repo):
    root, counts = _export(repo, scope="tools")
    graph = root.find("g:graph", NS)
    nodes = {n.get("id") for n in graph.findall("g:node", NS)}
    assert nodes == {"File:tools/c.py", "Function:tools/c.py::other"}
    assert counts["scope"] == "tools"


def test_cli_writes_the_file(repo, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "graph.graphml"
    args = argparse.Namespace(format="graphml", out=str(out), scope="", root=str(repo))
    cmd_export(args)
    root = _parse(out.read_text(encoding="utf-8"))
    assert root.find("g:graph", NS) is not None


def test_text_is_escaped(tmp_path, monkeypatch):
    class FakeConn:
        def find_nodes(self, label, **kwargs):
            if label != "File":
                return []
            return [{"path": str(tmp_path / 'a<&>"\x01.py'), "lang": "py"}]

        def find_neighbors(self, *args, **kwargs):
            return []

    buf = io.StringIO()
    write_graphml(FakeConn(), str(tmp_path), buf)
    root = _parse(buf.getvalue())
    node = root.find("g:graph/g:node", NS)
    assert node.get("id") == 'File:a<&>".py'
    assert _data(node, "n_label") == 'a<&>".py'
