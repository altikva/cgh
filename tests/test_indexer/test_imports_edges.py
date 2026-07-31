"""
Tests for IMPORTS edge ingestion (codegraph.imports.resolver + indexer wire-up).

The indexer must create File → File IMPORTS edges for resolvable imports
so MCP tools like `imports_of`, `who_imports`, and the recursive reach
query in `tools_query.py` actually return results. Pre-PR-A these edges
were silently dropped because the indexer never consumed idx.imports.
"""

from __future__ import annotations

import textwrap

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.imports.resolver import resolve_import, resolve_js_ts, resolve_python
from codegraph.indexer import index_file


@pytest.fixture(autouse=True)
def clean_db():
    reset_connection()
    yield
    reset_connection()


class TestResolvePython:
    def test_relative_same_dir(self, tmp_path):
        (tmp_path / "helpers.py").write_text("x = 1\n")
        importer = tmp_path / "main.py"
        importer.write_text("from . import helpers\n")
        result = resolve_python(".helpers", importer, tmp_path)
        assert result is not None
        assert result.name == "helpers.py"

    def test_relative_parent_dir(self, tmp_path):
        (tmp_path / "utils.py").write_text("y = 2\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        importer = sub / "main.py"
        importer.write_text("from .. import utils\n")
        result = resolve_python("..utils", importer, tmp_path)
        assert result is not None
        assert result.name == "utils.py"

    def test_absolute_from_repo_root(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text("z = 3\n")
        importer = tmp_path / "main.py"
        importer.write_text("from pkg import mod\n")
        result = resolve_python("pkg.mod", importer, tmp_path)
        assert result is not None
        assert result.name == "mod.py"

    def test_package_init(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        importer = tmp_path / "main.py"
        result = resolve_python("pkg", importer, tmp_path)
        assert result is not None
        assert result.name == "__init__.py"

    def test_unresolvable_returns_none(self, tmp_path):
        importer = tmp_path / "main.py"
        importer.write_text("")
        # 'requests' is not in repo — should fail to resolve.
        assert resolve_python("requests", importer, tmp_path) is None


class TestResolveJsTs:
    def test_relative_with_extension(self, tmp_path):
        (tmp_path / "utils.ts").write_text("export const x = 1;\n")
        importer = tmp_path / "main.ts"
        importer.write_text("import { x } from './utils';\n")
        result = resolve_js_ts("./utils", importer, tmp_path)
        assert result is not None
        assert result.name == "utils.ts"

    def test_relative_to_index_file(self, tmp_path):
        comp = tmp_path / "components"
        comp.mkdir()
        (comp / "index.tsx").write_text("export const X = 1;\n")
        importer = tmp_path / "main.ts"
        importer.write_text("import { X } from './components';\n")
        result = resolve_js_ts("./components", importer, tmp_path)
        assert result is not None
        assert result.name == "index.tsx"

    def test_parent_dir(self, tmp_path):
        (tmp_path / "shared.ts").write_text("export const y = 2;\n")
        sub = tmp_path / "src"
        sub.mkdir()
        importer = sub / "main.ts"
        importer.write_text("import { y } from '../shared';\n")
        result = resolve_js_ts("../shared", importer, tmp_path)
        assert result is not None
        assert result.name == "shared.ts"

    def test_bare_specifier_returns_none(self, tmp_path):
        importer = tmp_path / "main.ts"
        importer.write_text("")
        # 'react' is a node_modules package — not user code.
        assert resolve_js_ts("react", importer, tmp_path) is None
        assert resolve_js_ts("@scoped/pkg", importer, tmp_path) is None

    def test_alias_not_resolved_here(self, tmp_path):
        """Path aliases like '@/utils' are NOT handled by this baseline
        resolver. They come in a follow-up PR (feature/tsconfig-path-aliases).
        """
        importer = tmp_path / "main.ts"
        importer.write_text("")
        assert resolve_js_ts("@/utils", importer, tmp_path) is None


class TestResolveImportDispatch:
    def test_python_dispatch(self, tmp_path):
        (tmp_path / "lib.py").write_text("")
        importer = tmp_path / "main.py"
        assert resolve_import("python", ".lib", importer, tmp_path) is not None

    def test_typescript_dispatch(self, tmp_path):
        (tmp_path / "lib.ts").write_text("")
        importer = tmp_path / "main.ts"
        assert resolve_import("typescript", "./lib", importer, tmp_path) is not None

    def test_unknown_lang_returns_none(self, tmp_path):
        assert resolve_import("rust", "foo", tmp_path / "x.rs", tmp_path) is None


class TestImportsEdgesIndexed:
    def test_python_imports_edge_created(self, tmp_path):
        """from .helpers import x should produce a File → File IMPORTS edge."""
        (tmp_path / "helpers.py").write_text("def foo(): pass\n")
        main = tmp_path / "main.py"
        main.write_text(
            textwrap.dedent("""\
            from . import helpers

            def go():
                helpers.foo()
            """)
        )
        index_file(tmp_path / "helpers.py", tmp_path)
        index_file(main, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors(
            "IMPORTS",
            return_src=["path"],
            return_dst=["path"],
            return_edge=["symbol"],
        )
        assert any(
            "main.py" in e["src_path"] and "helpers.py" in e["dst_path"] for e in edges
        ), f"expected main.py → helpers.py IMPORTS edge, got {edges}"

    def test_typescript_imports_edge_created(self, tmp_path):
        utils = tmp_path / "utils.ts"
        utils.write_text("export const helper = () => 1;\n")
        main = tmp_path / "main.ts"
        main.write_text("import { helper } from './utils';\n")
        index_file(utils, tmp_path)
        index_file(main, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors("IMPORTS", return_src=["path"], return_dst=["path"])
        assert any(
            "main.ts" in e["src_path"] and "utils.ts" in e["dst_path"] for e in edges
        ), f"expected main.ts → utils.ts IMPORTS edge, got {edges}"

    def test_third_party_import_skipped(self, tmp_path):
        """Bare specifiers like 'react' shouldn't create edges to fake files."""
        main = tmp_path / "main.ts"
        main.write_text("import React from 'react';\n")
        index_file(main, tmp_path)

        conn = get_connection(tmp_path)
        assert conn.count_edges("IMPORTS") == 0

    def test_target_file_stub_created_on_demand(self, tmp_path):
        """If we index the importer before the target, the IMPORTS edge
        should still be created via a stub File node — when the target's
        own index_file runs later, the stub gets upserted with full data.
        """
        # Create the target file but don't index it yet
        target = tmp_path / "utils.ts"
        target.write_text("export const x = 1;\n")

        main = tmp_path / "main.ts"
        main.write_text("import { x } from './utils';\n")

        # Index main first — target File node should be created as a stub
        index_file(main, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors("IMPORTS", return_dst=["path"])
        assert len(edges) == 1
        assert "utils.ts" in edges[0]["dst_path"]

        # Now index the target — same node should be upserted with full data
        index_file(target, tmp_path)
        files_with_lang = [
            f
            for f in conn.find_nodes("File", return_fields=["path", "lang"])
            if f["path"].endswith("utils.ts")
        ]
        assert len(files_with_lang) == 1
        assert files_with_lang[0]["lang"] == "typescript"

    def test_imports_edges_purged_on_reindex(self, tmp_path):
        """Re-indexing a file should not duplicate IMPORTS edges or
        leave stale ones from removed imports."""
        utils = tmp_path / "utils.ts"
        utils.write_text("export const a = 1; export const b = 2;\n")
        main = tmp_path / "main.ts"
        main.write_text("import { a, b } from './utils';\n")
        index_file(utils, tmp_path)
        index_file(main, tmp_path)

        # Now main only imports a
        main.write_text("import { a } from './utils';\n")
        import time as _t

        _t.sleep(0.05)  # ensure mtime change
        index_file(main, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors(
            "IMPORTS",
            return_src=["path"],
            return_edge=["symbol"],
        )
        symbols = sorted(
            e["edge_symbol"] for e in edges if e["src_path"].endswith("main.ts")
        )
        assert "a" in symbols
        assert "b" not in symbols, (
            f"stale 'b' edge should have been purged, got {symbols}"
        )
