# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: shared_types on both backends: a Pydantic model in the parent
#              and the TypeScript interface mirroring it in a federated child
#              pair up as a deduced shared type (snake_case and camelCase
#              fields compare equal), unrelated same-named types and types
#              without fields do not, the child's graph is only read, and the
#              MCP tool and the `cgh shared-types` verb return the same view.
#              Federation config lives under tmp_path only.

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest
from rich.console import Console

import codegraph.cli.commands_query as cq
import codegraph.server as _srv
from codegraph.analysis.federation import add_subrepo
from codegraph.analysis.shared_types import match_shared_types, shared_types_report
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

BACKEND = {
    "app/__init__.py": "",
    "app/models.py": (
        "from pydantic import BaseModel\n\n\n"
        "class UserOut(BaseModel):\n"
        "    id: int\n"
        "    email: str\n"
        "    created_at: str\n"
        "    _cache: dict\n"
        "    model_config = {'frozen': True}\n\n\n"
        "class Invoice(BaseModel):\n"
        "    id: int\n"
        "    total: float\n\n\n"
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n"
    ),
}

FRONTEND = {
    "src/types.ts": (
        "export interface UserOut {\n"
        "  id: number;\n"
        "  email: string;\n"
        "  createdAt: string;\n"
        "  avatar?: string;\n"
        "  greet(): string;\n"
        "}\n\n"
        "export type Invoice = { number: string; issuedOn: string };\n\n"
        "export class Service {\n"
        "  run(): number {\n"
        "    return 1;\n"
        "  }\n"
        "}\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _graph_file(root: Path) -> Path:
    return next(p for p in (root / ".codegraph").glob("graph.*") if p.is_file())


@pytest.fixture(params=["duckdb", "sqlite"])
def fed(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    parent = tmp_path.resolve() / "api"
    child = parent / "web"
    _write(parent, BACKEND)
    _write(child, FRONTEND)
    reset_connection()
    index_repo(child, method="os_walk")
    reset_connection()
    add_subrepo(parent, child)
    index_repo(parent, method="os_walk")
    reset_connection()
    _srv._root = parent
    _srv._conn = None
    yield parent, child
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _report(parent: Path, **kw) -> dict:
    return shared_types_report(lambda: get_connection(parent), parent, **kw)


def test_pydantic_model_pairs_with_the_ts_interface(fed):
    parent, child = fed
    out = _report(parent)
    assert out["scopes"] == ["parent", "web"]
    assert [p["name"] for p in out["shared_types"]] == ["UserOut"]
    pair = out["shared_types"][0]
    assert pair["deduced"] is True
    assert pair["scopes"] == ["parent", "web"]
    assert pair["matched_fields"] == ["id", "email", "created_at"]
    assert pair["confidence"] == 0.75
    assert pair["only_in"] == {"parent": [], "web": ["avatar"]}
    files = {d["scope"]: Path(d["file"]) for d in pair["definitions"]}
    assert files == {
        "parent": parent / "app/models.py",
        "web": child / "src/types.ts",
    }


def test_thresholds_and_name_filter(fed):
    parent, _child = fed
    assert _report(parent, min_confidence=0.8)["shared_types"] == []
    assert _report(parent, name="Invoice")["shared_types"] == []
    assert [p["name"] for p in _report(parent, name="UserOut")["shared_types"]] == [
        "UserOut"
    ]


def test_child_graph_is_only_read(fed):
    parent, child = fed
    graph = _graph_file(child)
    before = (graph.stat().st_mtime_ns, graph.stat().st_size, graph.read_bytes())
    _report(parent)
    after = (graph.stat().st_mtime_ns, graph.stat().st_size, graph.read_bytes())
    assert before == after


def test_mcp_tool_matches_the_report(fed):

    class _FakeMcp:
        def __init__(self) -> None:
            self.tools: dict = {}

        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    from codegraph.server.tools_insight import register

    m = _FakeMcp()
    register(m)
    out = json.loads(m.tools["shared_types"]())
    assert [p["name"] for p in out["shared_types"]] == ["UserOut"]
    assert out["shared_types"][0]["deduced"] is True


def test_cli_verb_json_and_text(fed, capsys, monkeypatch):
    parent, _child = fed
    reset_connection()
    ns = argparse.Namespace(
        root=str(parent), name="", min_confidence=0.5, limit=100, json=True
    )
    cq.cmd_shared_types(ns)
    out = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in out["shared_types"]] == ["UserOut"]

    reset_connection()
    buf = io.StringIO()
    monkeypatch.setattr(cq, "console", Console(file=buf, width=200, no_color=True))
    ns.json = False
    cq.cmd_shared_types(ns)
    text = buf.getvalue()
    assert "UserOut" in text and "created_at" in text and "only in web: avatar" in text


def test_without_subrepos_the_note_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CGH_DB", "sqlite")
    root = tmp_path.resolve() / "solo"
    _write(root, BACKEND)
    reset_connection()
    index_repo(root, method="os_walk")
    try:
        out = _report(root)
    finally:
        reset_connection()
    assert out["shared_types"] == [] and out["scopes"] == ["parent"]
    assert "No federated subrepo" in out["note"]


def test_paths_outside_a_scope_are_not_read(tmp_path):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("class T:\n    a: int\n    b: int\n", encoding="utf-8")
    inside = tmp_path / "scope_b"
    inside.mkdir()
    (inside / "t.py").write_text("class T:\n    a: int\n    b: int\n", encoding="utf-8")
    row = {"name": "T", "start_line": 1, "end_line": 3}
    pairs, _ = match_shared_types(
        [
            ("a", tmp_path / "scope_a", [{**row, "file_path": str(outside)}]),
            ("b", inside, [{**row, "file_path": str(inside / "t.py")}]),
        ]
    )
    assert pairs == []
