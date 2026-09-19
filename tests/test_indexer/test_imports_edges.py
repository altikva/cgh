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
from codegraph.imports.resolver import (
    resolve_import,
    resolve_java,
    resolve_js_ts,
    resolve_python,
)
from codegraph.indexer import index_file


@pytest.fixture(autouse=True)
def clean_db():
    from codegraph.imports.resolver import reset_for_tests

    reset_for_tests()
    reset_connection()
    yield
    reset_for_tests()
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

    def test_absolute_under_src_layout(self, tmp_path):
        """A src/ layout resolved nothing at all: the repo root was the only
        anchor tried, so `from pkg.mod import x` looked for <root>/pkg/mod.py."""
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text("z = 3\n")
        importer = pkg / "main.py"
        importer.write_text("from pkg.mod import z\n")

        result = resolve_python("pkg.mod", importer, tmp_path)

        assert result == (pkg / "mod.py").resolve()

    def test_absolute_under_nested_package_root(self, tmp_path):
        """Packages living one directory down, imported as if that directory
        were the root: `cluster/utilities/` imported as `utilities`."""
        cluster = tmp_path / "cluster"
        utils = cluster / "utilities"
        utils.mkdir(parents=True)
        (cluster / "__init__.py").write_text("")
        (utils / "__init__.py").write_text("")
        (utils / "action.py").write_text("def go(): pass\n")
        importer = cluster / "bot.py"
        importer.write_text("from utilities.action import go\n")

        result = resolve_python("utilities.action", importer, tmp_path)

        assert result == (utils / "action.py").resolve()

    def test_absolute_beside_the_importer(self, tmp_path):
        """A script run from its own folder sees its siblings as top level."""
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "helpers.py").write_text("def h(): pass\n")
        importer = tools / "run.py"
        importer.write_text("from helpers import h\n")

        result = resolve_python("helpers", importer, tmp_path)

        assert result == (tools / "helpers.py").resolve()

    def test_repo_root_still_resolves_a_flat_layout(self, tmp_path):
        """The package nearest the importer wins, but a flat layout keeps
        resolving exactly as before."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text("z = 3\n")
        importer = tmp_path / "main.py"
        importer.write_text("from pkg.mod import z\n")

        result = resolve_python("pkg.mod", importer, tmp_path)

        assert result == (pkg / "mod.py").resolve()

    def test_package_directory_used_as_the_working_directory(self, tmp_path):
        """A service whose code lives in `app/` and runs from there imports
        its siblings by bare name, from a file nested one level deeper."""
        app = tmp_path / "app"
        (app / "providers").mkdir(parents=True)
        (app / "handlers").mkdir()
        (app / "__init__.py").write_text("")
        (app / "providers" / "__init__.py").write_text("")
        (app / "providers" / "apple.py").write_text("class Pass: pass\n")
        (app / "handlers" / "__init__.py").write_text("")
        importer = app / "handlers" / "apple_handler.py"
        importer.write_text("from providers.apple import Pass\n")

        result = resolve_python("providers.apple", importer, tmp_path)

        assert result == (app / "providers" / "apple.py").resolve()

    def test_external_dependency_still_unresolved(self, tmp_path):
        """More roots must not turn a third-party import into a false edge."""
        src = tmp_path / "src" / "app"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        importer = src / "main.py"
        importer.write_text("import fastapi\n")

        assert resolve_python("fastapi", importer, tmp_path) is None

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

    def test_tilde_alias_under_a_nuxt_app_directory(self, tmp_path):
        """Nuxt writes its tsconfig into .nuxt/, a build artifact nobody
        commits, so `~/x` has no alias on disk to resolve through."""
        web = tmp_path / "web"
        (web / "app" / "composables").mkdir(parents=True)
        (web / "app" / "pages").mkdir(parents=True)
        (web / "nuxt.config.ts").write_text("export default {}\n")
        target = web / "app" / "composables" / "useSeo.ts"
        target.write_text("export const useSeo = () => {}\n")
        importer = web / "app" / "pages" / "about.vue"
        importer.write_text(
            "<script setup>\nimport { useSeo } from '~/composables/useSeo'\n</script>\n"
        )

        result = resolve_js_ts("~/composables/useSeo", importer, tmp_path)

        assert result == target.resolve()

    def test_at_alias_under_a_vite_src_directory(self, tmp_path):
        (tmp_path / "src" / "utils").mkdir(parents=True)
        (tmp_path / "vite.config.ts").write_text("export default {}\n")
        target = tmp_path / "src" / "utils" / "format.ts"
        target.write_text("export const f = 1\n")
        importer = tmp_path / "src" / "main.ts"
        importer.write_text("import { f } from '@/utils/format'\n")

        result = resolve_js_ts("@/utils/format", importer, tmp_path)

        assert result == target.resolve()

    def test_scoped_package_is_not_treated_as_an_alias(self, tmp_path):
        """`@nuxt/ui` is a dependency. Only `@/` is the alias."""
        (tmp_path / "app").mkdir()
        (tmp_path / "package.json").write_text("{}")
        importer = tmp_path / "app" / "main.ts"
        importer.write_text("import x from '@nuxt/ui'\n")

        assert resolve_js_ts("@nuxt/ui", importer, tmp_path) is None

    def test_alias_does_not_invent_a_target(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "nuxt.config.ts").write_text("export default {}\n")
        importer = tmp_path / "app" / "main.ts"
        importer.write_text("import x from '~/composables/missing'\n")

        assert resolve_js_ts("~/composables/missing", importer, tmp_path) is None

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


class TestResolveJava:
    """A Java package maps to a directory chain under a source root, and the
    root is per module: guessing from the repo root misses every Maven or
    Gradle layout."""

    def _module(self, tmp_path):
        root = tmp_path / "svc" / "src" / "main" / "java" / "com" / "acme"
        root.mkdir(parents=True)
        (root / "Client.java").write_text("package com.acme;\n")
        (root / "Outer.java").write_text("package com.acme;\n")
        return root

    def test_resolves_under_the_maven_source_root(self, tmp_path):
        pkg = self._module(tmp_path)
        importer = pkg / "App.java"
        importer.write_text("import com.acme.Client;\n")

        hit = resolve_java("com.acme.Client", importer, tmp_path)

        assert hit == (pkg / "Client.java").resolve()

    def test_static_member_import_lands_on_the_declaring_file(self, tmp_path):
        """tree-sitter hands back com.acme.Outer.helper for a static import."""
        pkg = self._module(tmp_path)
        importer = pkg / "App.java"

        hit = resolve_java("com.acme.Outer.helper", importer, tmp_path)

        assert hit == (pkg / "Outer.java").resolve()

    def test_nested_class_lands_on_the_outer_file(self, tmp_path):
        pkg = self._module(tmp_path)
        importer = pkg / "App.java"

        hit = resolve_java("com.acme.Outer.Inner", importer, tmp_path)

        assert hit == (pkg / "Outer.java").resolve()

    def test_a_wildcard_resolves_to_nothing(self, tmp_path):
        """The asterisk is a separate node, so a wildcard arrives as the
        package alone. Resolving it to some file would invent an edge."""
        pkg = self._module(tmp_path)
        importer = pkg / "App.java"

        assert resolve_java("com.acme", importer, tmp_path) is None

    def test_the_jdk_stays_unresolved(self, tmp_path):
        pkg = self._module(tmp_path)
        importer = pkg / "App.java"

        assert resolve_java("java.io.IOException", importer, tmp_path) is None

    def test_a_sibling_module_is_not_reached_through_the_wrong_root(self, tmp_path):
        """Each module owns its source root; com.acme.Client in module A must
        not resolve into module B's tree."""
        self._module(tmp_path)
        other = tmp_path / "web" / "src" / "main" / "java" / "com" / "other"
        other.mkdir(parents=True)
        (other / "Page.java").write_text("package com.other;\n")
        importer = other / "App.java"

        assert resolve_java("com.other.Page", importer, tmp_path) is not None
        assert resolve_java("com.acme.Client", importer, tmp_path) is None


class TestResolveImportDispatch:
    def test_python_dispatch(self, tmp_path):
        (tmp_path / "lib.py").write_text("")
        importer = tmp_path / "main.py"
        assert resolve_import("python", ".lib", importer, tmp_path) is not None

    def test_typescript_dispatch(self, tmp_path):
        (tmp_path / "lib.ts").write_text("")
        importer = tmp_path / "main.ts"
        assert resolve_import("typescript", "./lib", importer, tmp_path) is not None

    def test_java_dispatch(self, tmp_path):
        pkg = tmp_path / "src" / "main" / "java" / "com" / "acme"
        pkg.mkdir(parents=True)
        (pkg / "Lib.java").write_text("package com.acme;\n")
        importer = pkg / "App.java"
        assert resolve_import("java", "com.acme.Lib", importer, tmp_path) is not None

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


class TestImportCoverage:
    """A scan records how many imports it parsed and how many it resolved.

    Without this, an empty import graph and a language with no resolver are
    the same silent answer, which is how whole repos sat at zero import edges
    without anyone noticing.
    """

    def _repo(self, tmp_path):
        import subprocess

        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("def a():\n    return 1\n")
        (pkg / "b.py").write_text("from pkg.a import a\nimport fastapi\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println(1) }\n'
        )
        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(
                ["git", *args], cwd=tmp_path, check=True, capture_output=True
            )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "init",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        return tmp_path

    def test_scan_records_resolved_and_unresolved_counts(self, tmp_path):
        from codegraph.indexer import index_repo
        from codegraph.state.scan_meta import scan_status

        root = self._repo(tmp_path)
        index_repo(str(root))

        coverage = scan_status(root)["imports"]
        assert coverage["python"]["seen"] == 2  # the sibling module and fastapi
        assert coverage["python"]["resolved"] == 1  # fastapi is not ours

    def test_a_language_without_a_resolver_reports_seen_but_unresolved(self, tmp_path):
        from codegraph.imports.resolver import RESOLVABLE_LANGS
        from codegraph.indexer import index_repo
        from codegraph.state.scan_meta import scan_status

        root = self._repo(tmp_path)
        index_repo(str(root))

        coverage = scan_status(root)["imports"]
        assert "go" not in RESOLVABLE_LANGS
        assert coverage["go"]["seen"] >= 1
        assert coverage["go"]["resolved"] == 0

    def test_a_src_layout_repo_does_not_come_back_empty(self, tmp_path):
        """The regression this whole branch exists for: imports parsed, none
        resolved, no error anywhere."""
        from codegraph.indexer import index_repo
        from codegraph.state.scan_meta import scan_status

        root = self._repo(tmp_path)
        index_repo(str(root))

        python = scan_status(root)["imports"]["python"]
        assert python["seen"] and python["resolved"], (
            "a src/ layout resolved nothing: every absolute import was anchored "
            "on the repo root alone"
        )
