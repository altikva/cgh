# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: JavaScript / TypeScript re-exports and dynamic imports, on both
#              backends. `export * from`, `export { a as b } from` and
#              `import('<literal>')` become IMPORTS edges marked by kind,
#              resolved through relative paths, tsconfig paths and workspace
#              packages; a run-time specifier is skipped; impact_of and the
#              `cgh impact` walk follow a chain of barrels without spending
#              depth on it. Also covers the TypeScript interface and object
#              type alias declarations the parser now records.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

REPO = {
    "tsconfig.json": json.dumps(
        {"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["src/lib/*"]}}}
    ),
    "package.json": json.dumps(
        {"name": "root", "private": True, "workspaces": ["packages/*"]}
    ),
    "src/lib/x.ts": "export function xf(): number {\n  return 1;\n}\n",
    "src/lib/y.ts": "export function yf(): number {\n  return 2;\n}\n",
    "src/lib/index.ts": ("export * from './x';\nexport { yf as why } from './y';\n"),
    # A barrel of the barrel, through a tsconfig path alias.
    "src/barrel2.ts": "export * from '@lib/index';\n",
    "src/app.ts": (
        "import { xf } from './barrel2';\n\n"
        "export function run(): number {\n  return xf();\n}\n"
    ),
    "src/lazy.ts": ("export async function load() {\n  return import('./lib/y');\n}\n"),
    "src/tpl.ts": (
        "export async function pick(n: string) {\n  return import(`./lib/${n}`);\n}\n"
    ),
    "packages/ui/package.json": json.dumps({"name": "@acme/ui", "main": "index.ts"}),
    "packages/ui/index.ts": "export * from './button';\n",
    "packages/ui/button.ts": "export function button(): string {\n  return 'b';\n}\n",
    "src/page.ts": (
        "import { button } from '@acme/ui';\n\n"
        "export function page(): string {\n  return button();\n}\n"
    ),
    "src/shapes.ts": (
        "export interface User extends Base {\n  id: number;\n  name?: string;\n}\n"
        "type Point = { x: number; y: number };\n"
        "type Id = string | number;\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    from codegraph.imports.resolver import reset_for_tests

    reset_for_tests()
    reset_connection()
    root = tmp_path.resolve() / "repo"
    _write(root, REPO)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    yield root
    reset_connection()
    reset_for_tests()
    _srv._root = None
    _srv._conn = None


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_insight import register as register_insight
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_insight(m)
    return m.tools


def _imports(root: Path, rel: str) -> list[tuple[str, str, str]]:
    out = json.loads(_tools()["imports_of"](rel))
    return sorted(
        (str(Path(i["module"]).relative_to(root)), i["symbol"], i["kind"])
        for i in out["imports"]
    )


def _impacted(root: Path, rel: str, depth: int) -> dict[str, str]:
    out = json.loads(_tools()["impact_of"](rel, max_depth=depth))
    assert out["direction"] == "importers"
    return {str(Path(r["node"]).relative_to(root)): r["via"] for r in out["impacted"]}


def test_reexports_are_import_edges_marked_reexport(repo):
    assert _imports(repo, "src/lib/index.ts") == [
        ("src/lib/x.ts", "", "reexport"),
        ("src/lib/y.ts", "yf", "reexport"),
    ]
    # Resolved through the tsconfig path alias.
    assert _imports(repo, "src/barrel2.ts") == [("src/lib/index.ts", "", "reexport")]


def test_dynamic_import_with_a_literal_is_an_edge(repo):
    assert _imports(repo, "src/lazy.ts") == [("src/lib/y.ts", "", "dynamic")]
    # A template specifier names no module: no edge.
    assert _imports(repo, "src/tpl.ts") == []


def test_plain_imports_keep_an_empty_kind(repo):
    assert _imports(repo, "src/app.ts") == [("src/barrel2.ts", "xf", "")]


def test_impact_follows_a_chain_of_barrels_in_one_hop(repo):
    impacted = _impacted(repo, "src/lib/x.ts", depth=1)
    assert impacted == {
        "src/lib/index.ts": "reexport",
        "src/barrel2.ts": "reexport",
        "src/app.ts": "import",
    }


def test_impact_reaches_dynamic_importers(repo):
    impacted = _impacted(repo, "src/lib/y.ts", depth=1)
    assert impacted["src/lazy.ts"] == "dynamic"
    # Through the index.ts re-export of yf.
    assert impacted["src/app.ts"] == "import"


def test_impact_through_a_workspace_package_barrel(repo):
    impacted = _impacted(repo, "packages/ui/button.ts", depth=1)
    assert impacted == {"packages/ui/index.ts": "reexport", "src/page.ts": "import"}


def test_cgh_impact_walk_follows_barrels(repo):
    from codegraph.analysis.impact import reverse_import_bfs

    via: dict[str, str] = {}
    files, truncated = reverse_import_bfs(
        get_connection(repo), [str(repo / "src/lib/x.ts")], max_depth=1, via=via
    )
    rel = {str(Path(f).relative_to(repo)) for f in files}
    assert rel == {"src/lib/index.ts", "src/barrel2.ts", "src/app.ts"}
    assert not truncated
    assert via[str(repo / "src/app.ts")] == "import"


def test_interfaces_and_object_types_are_classes(repo):
    conn = get_connection(repo)
    rows = conn.find_nodes(
        "Class",
        where={"file_path": str(repo / "src/shapes.ts")},
        return_fields=["name"],
    )
    # `type Id = string | number` declares no shape.
    assert sorted(r["name"] for r in rows) == ["Point", "User"]


def test_parser_records_reexport_and_dynamic_kinds(tmp_path):
    from codegraph.parsers.typescript import TypeScriptParser

    f = tmp_path / "m.ts"
    f.write_text(
        "export * as ns from './a';\n"
        "export type { T } from './b';\n"
        "export const k = 1;\n"
        "const f = () => import('./c');\n"
        "const g = (n: string) => import(n);\n",
        encoding="utf-8",
    )
    refs = [
        (i.source_module, i.symbols, i.kind)
        for i in TypeScriptParser().parse(f).imports
    ]
    assert refs == [
        ("./a", [], "reexport"),
        ("./b", ["T"], "reexport"),
        ("./c", [], "dynamic"),
    ]


def test_vue_script_reexports_and_dynamic_imports(tmp_path):
    from codegraph.parsers.vue import VueParser

    f = tmp_path / "C.vue"
    f.write_text(
        '<script setup lang="ts">\n'
        "import { ref } from 'vue';\n"
        "const Lazy = defineAsyncComponent(() => import('./Lazy.vue'));\n"
        "</script>\n"
        '<script lang="ts">\n'
        "export { helper as h } from './util';\n"
        "</script>\n",
        encoding="utf-8",
    )
    refs = [(i.source_module, i.symbols, i.kind) for i in VueParser().parse(f).imports]
    assert ("./Lazy.vue", [], "dynamic") in refs
    assert ("./util", ["helper"], "reexport") in refs
    assert ("vue", ["ref"], "") in refs
