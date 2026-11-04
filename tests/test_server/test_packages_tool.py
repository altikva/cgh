# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The packages MCP tool and the `cgh packages` verb over an
#              indexed monorepo: the listing with internal dependencies and
#              external counts, the per-package view ("who depends on X"),
#              and MCP servers reported with key names only. The CLI goes
#              through its local read-only path (no owner running).

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_arch import register as register_arch

FILES = {
    "package.json": {"name": "acme", "workspaces": ["packages/*"]},
    "packages/ui/package.json": {
        "name": "@acme/ui",
        "dependencies": {"@acme/core": "workspace:*", "react": "^18"},
    },
    "packages/core/package.json": {"name": "@acme/core", "version": "2.1.0"},
    ".mcp.json": {
        "mcpServers": {"graph": {"command": "cgh", "env": {"TOKEN": "hidden-value"}}}
    },
}


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def repo(tmp_path):
    for rel, body in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body), encoding="utf-8")
    reset_connection()
    for rel in FILES:
        assert index_file(tmp_path / rel, tmp_path)
    reset_connection()
    yield tmp_path
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _tool(root: Path):
    _srv._root = root
    _srv._conn = None
    m = _FakeMcp()
    register_arch(m)
    return m.tools["packages"]


def test_tool_lists_packages_and_servers(repo):
    out = json.loads(_tool(repo)())
    pkgs = {p["name"]: p for p in out["packages"]}
    assert set(pkgs) == {"acme", "@acme/ui", "@acme/core"}
    assert out["internal_edges"] == 1
    ui = pkgs["@acme/ui"]
    assert [(d["name"], d["file"]) for d in ui["internal"]] == [
        ("@acme/core", "packages/core/package.json")
    ]
    assert ui["external_count"] == 1
    assert ui["file"] == "packages/ui/package.json"
    assert out["cycles"] == []
    assert out["mcp_servers"] == [
        {
            "name": "graph",
            "transport": "stdio",
            "command": "cgh",
            "env_keys": ["TOKEN"],
            "file": ".mcp.json",
            "line": 1,
            "scope": "parent",
        }
    ]
    assert "hidden-value" not in json.dumps(out)


def test_tool_answers_who_depends_on_a_package(repo):
    out = json.loads(_tool(repo)(name="@acme/core"))
    assert [p["name"] for p in out["packages"]] == ["@acme/core"]
    core = out["packages"][0]
    assert core["dependents"] == [
        {"name": "@acme/ui", "group": "runtime", "spec": "workspace:*"}
    ]
    assert core["external"] == []
    assert core["version"] == "2.1.0"


def _cli(repo: Path, capsys, **kw) -> str:
    from codegraph.cli.commands_query import cmd_packages

    args = argparse.Namespace(
        root=str(repo),
        name=kw.get("name", ""),
        external=kw.get("external", False),
        limit=0,
        json=kw.get("json", False),
    )
    cmd_packages(args)
    return capsys.readouterr().out


def test_cli_json_matches_the_tool_shape(repo, capsys):
    out = json.loads(_cli(repo, capsys, json=True, external=True))
    ui = next(p for p in out["packages"] if p["name"] == "@acme/ui")
    assert [d["name"] for d in ui["external"]] == ["react"]
    assert [s["name"] for s in out["mcp_servers"]] == ["graph"]


def test_cli_text_output(repo, capsys):
    text = _cli(repo, capsys, name="core")
    assert "@acme/core" in text
    assert "<- @acme/ui workspace:*" in text
    text = _cli(repo, capsys)
    assert "-> @acme/core workspace:*" in text
    assert "MCP servers" in text and "keys: TOKEN" in text
    assert "hidden-value" not in text
