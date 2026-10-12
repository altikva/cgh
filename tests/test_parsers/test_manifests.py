# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: read_manifest on its own: which files it reads, Poetry and
#              PEP 735 dependency tables, unnamed and malformed manifests,
#              pnpm workspace globs, and the masking of credentials in MCP
#              server arguments and urls.

from __future__ import annotations

import json

from codegraph.parsers.manifests import package_key, read_manifest


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_only_manifests_are_read(tmp_path):
    assert read_manifest(_write(tmp_path / "tsconfig.json", "{}")) is None
    assert read_manifest(_write(tmp_path / "other" / "mcp.json", "{}")) is None
    assert read_manifest(_write(tmp_path / ".cursor" / "mcp.json", "{}")) is not None


def test_poetry_and_dependency_groups(tmp_path):
    m = read_manifest(
        _write(
            tmp_path / "pyproject.toml",
            '[tool.poetry]\nname = "Svc.Core"\nversion = "3.0"\n\n'
            "[tool.poetry.dependencies]\n"
            'python = "^3.11"\nhttpx = "^0.27"\n'
            'shared = { path = "../shared", develop = true }\n\n'
            "[tool.poetry.group.test.dependencies]\n"
            'pytest = "^8"\n\n'
            "[dependency-groups]\n"
            'lint = ["ruff>=0.5", {include-group = "test"}]\n',
        )
    )
    pkg = m.packages[0]
    assert (pkg.name, pkg.key, pkg.version) == ("Svc.Core", "python:svc-core", "3.0")
    assert {(d.name, d.spec, d.group) for d in pkg.deps} == {
        ("httpx", "^0.27", "runtime"),
        ("shared", "path:../shared", "runtime"),
        ("pytest", "^8", "group:test"),
        ("ruff", ">=0.5", "group:lint"),
    }


def test_unnamed_and_malformed(tmp_path):
    m = read_manifest(
        _write(tmp_path / "package.json", '{"devDependencies": {"x": "1"}}')
    )
    assert m.packages[0].name == "" and m.packages[0].key == ""
    assert (
        read_manifest(_write(tmp_path / "a" / "package.json", "{not json")).packages
        == []
    )
    broken = _write(tmp_path / "b" / "pyproject.toml", "[project\nname=")
    assert read_manifest(broken).packages == []


def test_pnpm_workspace_globs(tmp_path):
    _write(tmp_path / "pnpm-workspace.yaml", "packages:\n  - apps/*\n  - '!apps/old'\n")
    m = read_manifest(_write(tmp_path / "package.json", '{"name": "mono"}'))
    assert m.packages[0].workspaces == ["apps/*", "!apps/old"]
    app = read_manifest(
        _write(tmp_path / "apps" / "web" / "package.json", '{"name": "web"}')
    )
    assert app.packages[0].workspace_root == str(tmp_path)


def test_mcp_secrets_masked(tmp_path):
    cfg = {
        "mcpServers": {
            "db": {
                "command": "npx",
                "args": [
                    "-y",
                    "server-db",
                    "postgres://u:p@db.local/app",
                    "--password",
                    "x",
                ],
                "env": {"PGPASSWORD": "x"},
            },
            "plain": {"serverUrl": "https://mcp.example.com/sse?token=abc"},
        }
    }
    m = read_manifest(_write(tmp_path / ".mcp.json", json.dumps(cfg)))
    servers = {s.name: s for s in m.mcp_servers}
    assert servers["db"].args == [
        "-y",
        "server-db",
        "postgres://db.local/app",
        "--password",
        "***",
    ]
    assert servers["db"].env_keys == ["PGPASSWORD"]
    assert servers["plain"].transport == "http"
    assert servers["plain"].url == "https://mcp.example.com/sse?token=***"


def test_package_key():
    assert package_key("python", "Foo_Bar.baz") == "python:foo-bar-baz"
    assert package_key("npm", "@Scope/Pkg") == "npm:@Scope/Pkg"
    assert package_key("npm", "") == ""
