"""
Tests for workspace package resolution (npm / pnpm / yarn / lerna).

The TS resolver tries workspace packages after relative paths and
tsconfig aliases, so an import like `import { x } from "@wb/shared"`
inside a monorepo resolves to ``packages/shared/src/index.ts`` (or
whatever the package's entry point is).
"""

from __future__ import annotations

import textwrap

import pytest

from codegraph.imports.resolver import resolve_js_ts
from codegraph.imports.workspaces import (
    _clear_cache,
    _find_workspace_root,
    _parse_yaml_packages,
    _workspace_globs,
    load_packages,
    resolve_workspace_import,
)


@pytest.fixture(autouse=True)
def clear_workspace_cache():
    _clear_cache()
    yield
    _clear_cache()


class TestParseYamlPackages:
    def test_list_form(self):
        text = textwrap.dedent("""\
            packages:
              - apps/*
              - libs/*
              - "tools/internal/*"
            """)
        assert _parse_yaml_packages(text) == ["apps/*", "libs/*", "tools/internal/*"]

    def test_flow_form(self):
        text = "packages: ['apps/*', 'libs/*']"
        assert _parse_yaml_packages(text) == ["apps/*", "libs/*"]

    def test_comments_skipped(self):
        text = textwrap.dedent("""\
            # workspace config
            packages:
              - apps/*  # only mobile apps
              # libs is on hold
              - libs/*
            """)
        assert _parse_yaml_packages(text) == ["apps/*", "libs/*"]

    def test_block_ends_at_next_top_level_key(self):
        text = textwrap.dedent("""\
            packages:
              - apps/*
            extraField: ignored
            """)
        assert _parse_yaml_packages(text) == ["apps/*"]


class TestFindWorkspaceRoot:
    def test_finds_pnpm_workspace(self, tmp_path):
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
        deep = tmp_path / "apps" / "web" / "src"
        deep.mkdir(parents=True)
        assert _find_workspace_root(deep) == tmp_path.resolve()

    def test_finds_npm_workspace(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["apps/*"]}'
        )
        deep = tmp_path / "apps" / "web"
        deep.mkdir(parents=True)
        assert _find_workspace_root(deep) == tmp_path.resolve()

    def test_finds_lerna(self, tmp_path):
        (tmp_path / "lerna.json").write_text('{"packages": ["packages/*"]}')
        deep = tmp_path / "packages" / "shared"
        deep.mkdir(parents=True)
        assert _find_workspace_root(deep) == tmp_path.resolve()

    def test_no_workspace_returns_none(self, tmp_path):
        assert _find_workspace_root(tmp_path) is None


class TestWorkspaceGlobs:
    def test_pnpm(self, tmp_path):
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n  - libs/*\n")
        assert _workspace_globs(tmp_path) == ["apps/*", "libs/*"]

    def test_npm_list(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "r", "workspaces": ["apps/*"]}')
        assert _workspace_globs(tmp_path) == ["apps/*"]

    def test_yarn_object_form(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "r", "workspaces": {"packages": ["apps/*"]}}'
        )
        assert _workspace_globs(tmp_path) == ["apps/*"]

    def test_lerna(self, tmp_path):
        (tmp_path / "lerna.json").write_text('{"packages": ["packages/*"]}')
        assert _workspace_globs(tmp_path) == ["packages/*"]


class TestLoadPackages:
    def test_simple_npm_workspace(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@wb/shared", "main": "src/index.ts"}')

        packages = load_packages(tmp_path)
        assert "@wb/shared" in packages
        assert packages["@wb/shared"] == shared

    def test_unnamed_package_skipped(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        weird = tmp_path / "packages" / "no_name"
        weird.mkdir(parents=True)
        (weird / "package.json").write_text('{"version": "0.0.1"}')

        packages = load_packages(tmp_path)
        assert "no_name" not in packages

    def test_memoized(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@a/shared"}')

        first = load_packages(tmp_path)
        # mutate after first read
        (tmp_path / "packages" / "added").mkdir(parents=True)
        (tmp_path / "packages" / "added" / "package.json").write_text('{"name": "@a/added"}')

        second = load_packages(tmp_path)
        assert first == second

    def test_no_workspace_empty(self, tmp_path):
        assert load_packages(tmp_path) == {}


class TestResolveWorkspaceImport:
    def test_subpath_import(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@wb/shared"}')
        (shared / "auth.ts").write_text("export const a = 1;\n")

        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        candidates = resolve_workspace_import("@wb/shared/auth", importer.parent)
        assert any("auth" in str(c) for c in candidates)

    def test_bare_package_uses_main(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@wb/shared", "main": "dist/index.js"}')

        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        candidates = resolve_workspace_import("@wb/shared", importer.parent)
        assert any("dist/index.js" in str(c) for c in candidates)

    def test_bare_package_falls_back_to_index(self, tmp_path):
        """When package.json has no main/module/exports, the resolver tries
        standard index.ts / src/index.ts fallbacks."""
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@wb/shared"}')

        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        candidates = resolve_workspace_import("@wb/shared", importer.parent)
        assert any("src/index.ts" in str(c).replace("\\", "/") for c in candidates)

    def test_unknown_package_empty(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        assert resolve_workspace_import("not-in-workspace", importer.parent) == []


class TestResolveJsTsWithWorkspace:
    def test_workspace_import_resolves(self, tmp_path):
        """End-to-end: a workspace package import resolves to the real file."""
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        src = shared / "src"
        src.mkdir()
        (src / "index.ts").write_text("export const x = 1;\n")
        (shared / "package.json").write_text(
            '{"name": "@wb/shared", "main": "src/index.ts"}'
        )

        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        importer.write_text("")

        result = resolve_js_ts("@wb/shared", importer, tmp_path)
        assert result is not None
        assert result.name == "index.ts"
        assert "shared" in str(result)

    def test_workspace_subpath_import_resolves(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "root", "workspaces": ["packages/*"]}'
        )
        shared = tmp_path / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text('{"name": "@wb/shared"}')
        (shared / "auth.ts").write_text("export const a = 1;\n")

        importer = tmp_path / "apps" / "web" / "main.ts"
        importer.parent.mkdir(parents=True)
        importer.write_text("")

        result = resolve_js_ts("@wb/shared/auth", importer, tmp_path)
        assert result is not None
        assert result.name == "auth.ts"
