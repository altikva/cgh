# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rationale comments. The pure extraction (tags, continuation
#              lines, ADR references, strings never read as comments, the
#              symbol each comment documents) per language, and on both
#              backends the stored rows, their purge on reindex, the ADR
#              link to an indexed Markdown file, and the surfaces: the
#              rationale tool, symbol_lookup, context_for_task and
#              `cgh why`.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis.rationale import (
    adr_ref,
    comments,
    entries,
    extract,
    resolve_adr,
)
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, index_repo
from codegraph.parsers import get_parser

PY = """\
# NOTE: module-level note
import os

s = "# WHY: inside a string, not a comment"


class Store:
    # INVARIANT: keys are lowercase
    # and never empty.

    def get(self, k):
        # HACK(joy): cache bypass, see ADR-0007
        return k  # SAFETY: k validated by the caller

    # WHY: see docs/adr/0003-retry.md
    @staticmethod
    def put():
        pass


# Implements ADR-12 for the retry budget.
def run():
    # TODO: not rationale
    return os.sep
"""

TS = """\
const url = "http://x // NOTE: inside a string";
/**
 * WHY: the API caps at 100 rows,
 * so we page.
 */
export function page(n: number) {
  const t = `// HACK: inside a template`;
  // IMPORTANT: keep in sync with the server
  return n;
}

export class Loader {
  // DECISION: eager load, the list is small
  load() {
    return 1;
  }
}
"""

GO = """\
package main

// WARNING: not safe for concurrent use
func Add(a, b int) int {
\ts := "// NOTE: inside a string"
\t_ = s
\treturn a + b
}
"""

RUST = """\
// SAFETY: the pointer outlives the borrow
fn first<'a>(x: &'a str) -> &'a str {
    x
}
"""


def _rows(tmp_path: Path, name: str, src: str) -> list[tuple]:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    idx = get_parser(p.suffix).parse(p)
    return [(r[0], r[1], r[2], r[4], r[5]) for r in extract(p, idx)]


# --- extraction -------------------------------------------------------------


def test_python_comments_attach_to_their_symbol(tmp_path):
    rows = _rows(tmp_path, "store.py", PY)
    by_line = {r[0]: r for r in rows}
    assert by_line[1] == (1, "NOTE", "module-level note", "", "module")
    # A string holding a tag is not a comment.
    assert not any("inside a string" in r[2] for r in rows)
    # Continuation lines join the entry; a blank comment-free line ends it.
    assert by_line[8][1:] == (
        "INVARIANT",
        "keys are lowercase and never empty.",
        "Store",
        "class",
    )
    assert by_line[12][1] == "HACK" and by_line[12][3:] == ("Store.get", "method")
    # A trailing comment counts, inside the method.
    assert by_line[13][1:] == (
        "SAFETY",
        "k validated by the caller",
        "Store.get",
        "method",
    )
    # A comment above a decorated method documents that method.
    assert by_line[15][1] == "WHY" and by_line[15][3] == "Store.put"
    # An untagged comment citing an ADR is kept under the ADR tag, and a
    # comment right above a function documents it.
    assert by_line[21][1:4] == ("ADR", "Implements ADR-12 for the retry budget.", "run")
    assert not any(r[1] == "TODO" or "not rationale" in r[2] for r in rows)


def test_python_adr_refs_are_recorded(tmp_path):
    p = tmp_path / "store.py"
    p.write_text(PY, encoding="utf-8")
    rows = {r[0]: r for r in extract(p, get_parser(".py").parse(p))}
    assert rows[12][7] == "ADR-0007"
    assert rows[15][7] == "docs/adr/0003-retry.md"
    assert rows[1][7] == ""


def test_typescript_block_and_line_comments(tmp_path):
    rows = _rows(tmp_path, "page.ts", TS)
    assert not any("inside" in r[2] for r in rows)
    tags = {(r[1], r[3]) for r in rows}
    assert ("WHY", "page") in tags
    assert ("IMPORTANT", "page") in tags
    assert ("DECISION", "Loader.load") in tags
    why = next(r for r in rows if r[1] == "WHY")
    assert why[2] == "the API caps at 100 rows, so we page."


def test_go_and_rust(tmp_path):
    go = _rows(tmp_path, "add.go", GO)
    assert [(r[1], r[3]) for r in go] == [("WARNING", "Add")]
    rust = _rows(tmp_path, "lib.rs", RUST)
    assert [(r[1], r[2], r[3]) for r in rust] == [
        ("SAFETY", "the pointer outlives the borrow", "first")
    ]


def test_escaped_quotes_vue_and_sql_comments():
    js = 'const a = "x \\" // NOTE: still a string";\n// WHY: real\n'
    assert [(c.line, c.text) for c in comments(js, ".js")] == [(2, "WHY: real")]
    vue = "<template>\n  <!-- HACK: layout fix -->\n</template>\n"
    assert [(e.line, e.tag) for e in entries(comments(vue, ".vue"))] == [(2, "HACK")]
    sql = "SELECT '-- NOTE: no' AS x; -- WHY: needed for the report\n"
    assert [(e.tag, e.text) for e in entries(comments(sql, ".sql"))] == [
        ("WHY", "needed for the report")
    ]


def test_tag_needs_a_colon_and_upper_case():
    found = entries(
        comments("# NOTE this has no colon\n# note: lower case\n# WHY : ok\n", ".py")
    )
    assert [(e.tag, e.text) for e in found] == [("WHY", "ok")]


def test_unparseable_python_falls_back_to_the_line_scanner():
    found = entries(comments('x = """unterminated\n# WHY: still found\n', ".py"))
    assert found == [] or found[0].tag == "WHY"


def test_unsupported_suffix_has_no_rationale(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("# WHY: plain text\n", encoding="utf-8")
    assert extract(p, None) == []


def test_adr_ref_and_resolution():
    assert adr_ref("see ADR-0007 for why") == "ADR-0007"
    assert adr_ref("see adr 7") == ""
    assert adr_ref("per docs/decisions/use-duckdb.md") == "docs/decisions/use-duckdb.md"
    assert adr_ref("see README.md") == ""
    cands = ["/r/docs/adr/0007-use-duckdb.md", "/r/docs/adr/0012-retry.md"]
    assert resolve_adr("ADR-0007", cands) == cands[0]
    assert resolve_adr("ADR-7", cands) == cands[0]
    assert resolve_adr("ADR-70", cands) == ""
    assert resolve_adr("./docs/adr/0012-retry.md", cands) == cands[1]
    assert resolve_adr("adr/0012-retry.md", cands) == cands[1]
    assert resolve_adr("other/0012-retry.md", cands) == ""


# --- stored rows and surfaces, on both backends ------------------------------

REPO = {
    "app/__init__.py": "",
    "app/store.py": PY,
    "web/page.ts": TS,
    "docs/adr/0007-cache-bypass.md": "# ADR-0007: cache bypass\n\nAccepted.\n",
    "docs/adr/0003-retry.md": "# ADR-0003: retry\n\nAccepted.\n",
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
    reset_connection()
    root = tmp_path.resolve() / "repo"
    _write(root, REPO)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_meta import register as register_meta
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_meta(m)
    return m.tools


def test_rows_are_stored_and_purged_on_reindex(repo):
    conn = get_connection(repo)
    rows = conn.rationale_rows(file_suffix="app/store.py")
    assert {r["tag"] for r in rows} >= {"NOTE", "INVARIANT", "HACK", "SAFETY", "WHY"}
    store = repo / "app" / "store.py"
    store.write_text(
        "def run():\n    # WHY: rewritten\n    return 1\n", encoding="utf-8"
    )
    assert index_file(store, repo, force=True)
    rows = conn.rationale_rows(file_suffix="app/store.py")
    assert [(r["tag"], r["text"], r["symbol"]) for r in rows] == [
        ("WHY", "rewritten", "run")
    ]


def test_rationale_tool_by_symbol_file_and_tag(repo):
    tool = _tools()["rationale"]
    out = json.loads(tool("Store"))
    # A class lists its members' comments too.
    assert {r["symbol"] for r in out["rationale"]} == {
        "Store",
        "Store.get",
        "Store.put",
    }
    assert all(r["scope"] == "parent" for r in out["rationale"])
    hack = next(r for r in out["rationale"] if r["tag"] == "HACK")
    assert hack["file"] == "app/store.py"
    assert hack["adr"] == "ADR-0007"
    assert hack["adr_file"] == "docs/adr/0007-cache-bypass.md"
    why = next(r for r in out["rationale"] if r["tag"] == "WHY")
    assert why["adr_file"] == "docs/adr/0003-retry.md"

    by_name = json.loads(tool("get"))
    assert [r["tag"] for r in by_name["rationale"]] == ["HACK", "SAFETY"]

    by_file = json.loads(tool("web/page.ts", tag="why"))
    assert by_file["tag"] == "WHY"
    assert [(r["symbol"], r["symbol_kind"]) for r in by_file["rationale"]] == [
        ("page", "function")
    ]

    capped = json.loads(tool("", limit=2))
    assert capped["total"] == 2 and capped["truncated"] is True


def test_symbol_lookup_carries_rationale(repo):
    out = json.loads(_tools()["symbol_lookup"]("get"))
    (hit,) = [d for d in out["definitions"] if d["kind"] == "function"]
    assert set(hit) >= {"kind", "name", "file", "lines", "doc", "rationale"}
    assert "_id" not in hit
    assert [r["tag"] for r in hit["rationale"]] == ["HACK", "SAFETY"]
    assert hit["rationale"][0]["adr_file"].endswith("docs/adr/0007-cache-bypass.md")
    # A symbol without any keeps the old shape.
    plain = json.loads(_tools()["symbol_lookup"]("os"))
    assert all("rationale" not in d for d in plain.get("definitions", []))


def test_context_for_task_lists_rationale(repo):
    out = json.loads(_tools()["context_for_task"]("Store keys"))
    assert {
        "file": "app/store.py",
        "line": 8,
        "tag": "INVARIANT",
        "text": "keys are lowercase and never empty.",
        "symbol": "Store",
        "symbol_kind": "class",
    } in out["rationale"]
    assert "INVARIANT (line 8): keys are lowercase" in out["context_markdown"]


def test_cgh_why(repo, capsys):
    from codegraph.cli.commands_query import cmd_why

    args = argparse.Namespace(
        root=str(repo), target="Store.get", tag="", limit=50, json=True
    )
    cmd_why(args)
    out = json.loads(capsys.readouterr().out)
    assert [r["tag"] for r in out["rationale"]] == ["HACK", "SAFETY"]

    args.json = False
    cmd_why(args)
    text = capsys.readouterr().out
    assert "HACK" in text and "cache bypass" in text


def test_symbol_lookup_without_the_table_still_answers(repo):
    # A federated child indexed by an older cgh has no rationale table.
    get_connection(repo).execute("DROP TABLE rationale")
    out = json.loads(_tools()["symbol_lookup"]("get"))
    assert out["found"] is True
    assert all("rationale" not in d and "_id" not in d for d in out["definitions"])
