# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Package manifests and MCP client configs in the graph, on both
#              backends: Package nodes with DEPENDS_ON edges between the
#              packages of one repo whatever order their manifests are
#              indexed in, external dependencies kept as name and spec only,
#              edges dropped and restored when a manifest is reindexed,
#              internal cycles, and MCP servers stored without any env or
#              header value. Also covers discovery of the dot-path configs.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codegraph.analysis.packages import collect, packages_payload
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, skipped_path_parts


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    yield request.param
    reset_connection()


def _write(root: Path, files: dict[str, object]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, indent=2)
        p.write_text(text, encoding="utf-8")


NPM_REPO = {
    "package.json": {
        "name": "acme",
        "private": True,
        "workspaces": ["packages/*"],
        "devDependencies": {"typescript": "^5.4"},
    },
    "packages/ui/package.json": {
        "name": "@acme/ui",
        "version": "1.0.0",
        "dependencies": {"@acme/core": "workspace:*", "react": "^18.2.0"},
    },
    "packages/core/package.json": {
        "name": "@acme/core",
        "version": "1.0.0",
        "dependencies": {"zod": "^3.22"},
    },
}


def _payload(root: Path, **kw) -> dict:
    conn = get_connection(root)
    return packages_payload(
        [("parent", collect(conn))],
        short_path=lambda p: str(Path(p).relative_to(root)),
        **kw,
    )


def _by_name(payload: dict) -> dict[str, dict]:
    return {p["name"]: p for p in payload["packages"]}


@pytest.mark.parametrize("core_first", [True, False])
def test_npm_workspace_internal_and_external(tmp_path, backend, core_first):
    _write(tmp_path, NPM_REPO)
    order = ["packages/ui/package.json", "packages/core/package.json"]
    if core_first:
        order.reverse()
    for rel in ["package.json", *order]:
        assert index_file(tmp_path / rel, tmp_path)

    pkgs = _by_name(_payload(tmp_path, include_external=True))
    ui = pkgs["@acme/ui"]
    assert [(d["name"], d["spec"], d["group"]) for d in ui["internal"]] == [
        ("@acme/core", "workspace:*", "runtime")
    ]
    assert [(d["name"], d["spec"]) for d in ui["external"]] == [("react", "^18.2.0")]
    assert ui["workspace_root"] == str(tmp_path)
    core = pkgs["@acme/core"]
    assert [d["name"] for d in core["dependents"]] == ["@acme/ui"]
    assert core["internal"] == []
    assert pkgs["acme"]["workspaces"] == ["packages/*"]
    assert "workspace_root" not in pkgs["acme"]


def test_reindex_of_target_keeps_the_edge(tmp_path, backend):
    _write(tmp_path, NPM_REPO)
    for rel in NPM_REPO:
        assert index_file(tmp_path / rel, tmp_path)
    assert index_file(tmp_path / "packages/core/package.json", tmp_path, force=True)
    ui = _by_name(_payload(tmp_path))["@acme/ui"]
    assert [d["name"] for d in ui["internal"]] == ["@acme/core"]
    assert ui["external_count"] == 1
    assert "external" not in ui

    # Renaming core turns the dependency external.
    _write(
        tmp_path,
        {"packages/core/package.json": {"name": "@acme/kernel", "version": "2.0.0"}},
    )
    assert index_file(tmp_path / "packages/core/package.json", tmp_path, force=True)
    ui = _by_name(_payload(tmp_path, name="@acme/ui"))["@acme/ui"]
    assert ui["internal"] == []
    assert {d["name"] for d in ui["external"]} == {"@acme/core", "react"}


def test_python_uv_workspace_and_name_normalization(tmp_path, backend):
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "root-app"\nversion = "0.1.0"\n'
                'dependencies = ["My_Lib>=1.0", "requests[socks]>=2"]\n\n'
                "[dependency-groups]\n"
                'dev = ["pytest>=8"]\n\n'
                "[tool.uv.workspace]\n"
                'members = ["libs/*"]\n\n'
                "[tool.uv.sources]\n"
                "my-lib = { workspace = true }\n"
            ),
            "libs/mylib/pyproject.toml": (
                '[project]\nname = "my-lib"\nversion = "1.2.0"\n'
                "dependencies = []\n\n"
                "[project.optional-dependencies]\n"
                'cli = ["root-app"]\n'
            ),
            "tools/pyproject.toml": "[tool.ruff]\nline-length = 88\n",
        },
    )
    for rel in ("pyproject.toml", "libs/mylib/pyproject.toml", "tools/pyproject.toml"):
        assert index_file(tmp_path / rel, tmp_path)

    payload = _payload(tmp_path, include_external=True)
    pkgs = _by_name(payload)
    # A pyproject.toml that only configures tools declares no package.
    assert set(pkgs) == {"root-app", "my-lib"}
    root = pkgs["root-app"]
    assert [(d["name"], d["spec"]) for d in root["internal"]] == [("my-lib", ">=1.0")]
    assert {(d["name"], d["group"]) for d in root["external"]} == {
        ("requests", "runtime"),
        ("pytest", "dev"),
    }
    assert root["workspaces"] == ["libs/*"]
    lib = pkgs["my-lib"]
    assert lib["workspace_root"] == str(tmp_path)
    assert [(d["name"], d["group"]) for d in lib["internal"]] == [
        ("root-app", "optional:cli")
    ]
    assert payload["cycles"] == [
        {"scope": "parent", "packages": ["my-lib", "root-app"]}
    ]


def test_mcp_servers_keep_no_secret_values(tmp_path, backend):
    _write(
        tmp_path,
        {
            ".mcp.json": {
                "mcpServers": {
                    "graph": {
                        "command": "cgh",
                        "args": ["serve", "--api-key", "sk-live-123", "--token=abc"],
                        "env": {"API_TOKEN": "s3cr3t-value"},
                    },
                    "inline": {"command": "npx -y server ghp_inlinetoken"},
                    "remote": {
                        "type": "http",
                        "url": "https://user:pw@mcp.example.com/v1?api_key=xyz&x=1",
                        "headers": {"Authorization": "Bearer t0k3n"},
                    },
                }
            },
            ".vscode/mcp.json": (
                '{\n  // editor servers\n  "servers": {\n'
                '    "docs": {"type": "sse", "url": "https://docs.example.com/sse",},\n'
                "  },\n}\n"
            ),
        },
    )
    for rel in (".mcp.json", ".vscode/mcp.json"):
        assert index_file(tmp_path / rel, tmp_path)

    servers = {s["name"]: s for s in _payload(tmp_path)["mcp_servers"]}
    assert set(servers) == {"graph", "inline", "remote", "docs"}
    graph = servers["graph"]
    assert graph["transport"] == "stdio"
    assert graph["command"] == "cgh"
    assert graph["arg_count"] == 4
    assert "args" not in graph
    assert servers["inline"]["command"] == "npx"
    assert graph["env_keys"] == ["API_TOKEN"]
    assert graph["file"] == ".mcp.json"
    remote = servers["remote"]
    assert remote["transport"] == "http"
    assert remote["url"] == "https://mcp.example.com/v1"
    assert remote["header_keys"] == ["Authorization"]
    assert servers["docs"]["transport"] == "sse"
    assert servers["docs"]["file"] == ".vscode/mcp.json"

    # No secret value reaches the graph, in any table.
    conn = get_connection(tmp_path)
    for table in ("mcp_server", "md_section", "name_ref"):
        res = conn.execute(f"SELECT * FROM {table}")
        while res.has_next():
            row = " ".join(str(v) for v in res.get_next())
            for secret in (
                "s3cr3t-value",
                "t0k3n",
                "sk-live-123",
                "abc",
                "xyz",
                ":pw@",
                "ghp_inlinetoken",
            ):
                assert secret not in row


def test_dot_path_filter_lets_only_mcp_configs_through():
    assert not skipped_path_parts((".mcp.json",))
    assert not skipped_path_parts((".cursor", "mcp.json"))
    assert not skipped_path_parts(("apps", "web", ".vscode", "mcp.json"))
    assert skipped_path_parts((".github", "workflows", "ci.yml"))
    assert skipped_path_parts((".cursor", "rules.json"))
    assert skipped_path_parts((".hidden", ".mcp.json"))
    assert skipped_path_parts(("node_modules", "x", "package.json"))
    assert not skipped_path_parts(("apps", "package.json"))
