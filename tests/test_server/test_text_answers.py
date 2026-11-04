# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The grep-like text answers of pattern_search, find_callers,
#              search_symbols and symbol_lookup. A reference decoder reads
#              the text back, so each test checks that text and JSON carry
#              the same rows and fields. Also: the ceiling held on the text as
#              received, federated sections, the [mcp] answer_format setting
#              and CGH_ANSWER_FORMAT, and CLI and hook callers kept on JSON.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server._budget import wire_bytes
from codegraph.server._text import (
    _LOC_KEY,
    _POSITIONAL,
    _SECONDARY,
    TEXT_TOOLS,
    render_text,
    resolve_format,
    text_answer,
)

# ---------------------------------------------------------------------------
# Reference decoder
# ---------------------------------------------------------------------------

_KEY = re.compile(r"[A-Za-z_]\w*=")
_LOC = re.compile(r"\d+(?:-\d+)?")


def _read_tok(s: str, i: int) -> tuple[Any, int]:
    if i < len(s) and s[i] in '"[{-0123456789tfn':
        try:
            value, j = json.JSONDecoder().raw_decode(s, i)
            if j == len(s) or s[j] == " ":
                return value, j
        except ValueError:
            pass
    j = s.find(" ", i)
    j = len(s) if j < 0 else j
    return s[i:j], j


def _tokens(s: str) -> tuple[list[Any], dict[str, Any]]:
    pos: list[Any] = []
    kv: dict[str, Any] = {}
    i = 0
    while i < len(s):
        if s[i] == " ":
            i += 1
            continue
        m = _KEY.match(s, i)
        if m:
            key = m.group()[:-1]
            if key == "top_dirs" and s[m.end() : m.end() + 1] != "[":
                raw, i = _read_tok(s, m.end())
                kv[key] = [
                    {"glob": g, "hits": int(n)}
                    for g, n in (p.rsplit(":", 1) for p in raw.split(","))
                ]
                continue
            kv[key], i = _read_tok(s, m.end())
        else:
            value, i = _read_tok(s, i)
            pos.append(value)
    return pos, kv


def _cell(s: str) -> str:
    return json.loads(s) if s.startswith('"') else s


def _path(s: str) -> tuple[str, str]:
    """(path, rest of the line) for a line opening with a path."""
    if s.startswith('"'):
        value, j = json.JSONDecoder().raw_decode(s)
        return value, s[j:]
    return s, ""


def _row(body: str, name: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    first, _, rest = body.partition(" ")
    if _LOC.fullmatch(first):
        row[_LOC_KEY[name]] = int(first) if _LOC_KEY[name] == "line" else first
        body = rest
    pos, kv = _tokens(body)
    for key, value in zip(_POSITIONAL[name], pos, strict=False):
        row[key] = value
    row.update(kv)
    return row


def decode(tool: str, text: str) -> dict[str, Any]:
    lines = text.split("\n")
    _pos, out = _tokens(lines[0])
    primary = TEXT_TOOLS[tool]
    mode, scope, file = primary, None, None
    for ln in lines[1:]:
        if ln.startswith("[cut]"):
            mode = "more"
            out["more"] = {}
            continue
        if ln.startswith("[") and ln.endswith("]"):
            mode, scope, file = primary, ln[1:-1], None
            continue
        if ln[:-1] in _SECONDARY and ln.endswith(":"):
            mode, scope, file = ln[:-1], None, None
            out.setdefault(mode, [])
            continue
        rows = out.setdefault(mode, []) if mode != "more" else None
        if ln.startswith("  "):
            body = ln[2:]
            if mode == "more":
                path, n = body.rsplit(" +", 1)
                out["more"][_path(path)[0] if path.startswith('"') else path] = int(n)
            elif mode == "files":
                line, _, rest = body.partition(": ")
                rows[-1]["hits"].append([int(line), _cell(rest)])
            elif mode == "definitions":
                if body.startswith("doc="):
                    rows[-1]["doc"] = _tokens(body)[1]["doc"]
                else:
                    rows[-1]["doc"] = _cell(body)
            else:
                row = {"file": file, **_row(body, mode)}
                if scope is not None:
                    row["scope"] = scope
                rows.append(row)
            continue
        # A line of its own: a path, a definition or a row without a file.
        if mode == "files":
            path, rest = _path(ln)
            if not ln.startswith('"'):
                path, _, rest = ln.partition(" ")
            group: dict[str, Any] = {}
            if scope is not None:
                group["scope"] = scope
            if out.get("count_only"):
                if rest.startswith(":"):
                    count = int(rest[1:].split(" ")[0])
                else:
                    path, count = path.rsplit(":", 1)
                    count = int(count)
                group.update({"file": path, "count": count})
            else:
                group.update({"file": path, "hits": []})
                group.update(_tokens(rest)[1])
            rows.append(group)
        elif mode == "definitions":
            if ln.startswith("- ") or ln == "-":
                row = _row(ln[2:], mode)
            else:
                if ln.startswith('"'):
                    path, rest = _path(ln)
                    loc, _, body = rest[1:].partition(" ")
                else:
                    head, _, body = ln.partition(" ")
                    path, loc = head.rsplit(":", 1)
                row = {"file": path, **_row(f"{loc} {body}".strip(), mode)}
            if scope is not None:
                row["scope"] = scope
            rows.append(row)
        elif ln.startswith("- "):
            file = None
            row = _row(ln[2:], mode)
            if scope is not None:
                row["scope"] = scope
            rows.append(row)
        else:
            file = _path(ln)[0]
    return out


def _clean(node: Any, top: bool = True) -> Any:
    """The JSON answer the way the text writes it: null and empty values
    left out of the header and the rows."""
    if isinstance(node, dict):
        return {
            k: _clean(v, False) if isinstance(v, list) else v
            for k, v in node.items()
            if v is not None and v != ""
        }
    if isinstance(node, list):
        return [_clean(x, False) if isinstance(x, dict) else x for x in node]
    return node


def _same(tool: str, payload: dict[str, Any]) -> str:
    text = render_text(tool, json.loads(json.dumps(payload)))
    assert decode(tool, text) == _clean(payload), text
    return text


# ---------------------------------------------------------------------------
# Same rows and fields, per tool
# ---------------------------------------------------------------------------


def test_pattern_search_hits_round_trip():
    payload = {
        "pattern": "wire_bytes",
        "glob": None,
        "backend": "git-grep",
        "total": 5,
        "files": [
            {
                "file": "codegraph/server/_budget.py",
                "hits": [[19, "x = 1"], [113, "def wire_bytes(text: str) -> int:"]],
            },
            {
                "file": "docs/a b.md",
                "hits": [
                    [3, "line\twith tab"],
                    [4, '"quoted" start'],
                    [5, "two\nlines"],
                ],
            },
            {
                "file": "tests/é.py",
                "hits": [[7, "  leading blank"], [8, ""], [9, "naïve: café"]],
            },
        ],
        "confidence": "high",
        "root": "/work/project",
    }
    text = _same("pattern_search", payload)
    assert "\n  113: def wire_bytes(text: str) -> int:" in text
    assert '\n  3: "line\\twith tab"' in text
    assert '\n  4: "\\"quoted\\" start"' in text
    assert '\n"docs/a b.md"\n' in text
    assert "\n  9: naïve: café" in text


def test_pattern_search_cut_answer_names_what_was_left_out():
    payload = {
        "pattern": "import",
        "glob": "*.py",
        "backend": "rg",
        "total": 40,
        "files": [{"file": "a.py", "hits": [[1, "import os"]]}],
        "shown": 1,
        "truncated": True,
        "more": {"a.py": 3, "b c.py": 36},
        "more_files": 2,
        "top_dirs": [{"glob": "pkg/**", "hits": 30}, {"glob": "tests/**", "hits": 10}],
        "hint": "39 more hit(s) not shown.",
        "confidence": "medium",
        "next": 'pattern_search("import", glob="pkg/**")',
    }
    text = _same("pattern_search", payload)
    header = text.split("\n")[0]
    assert "truncated=true" in header
    assert "top_dirs=pkg/**:30,tests/**:10" in header
    assert "[cut] 39 more hit(s) not shown, per file:" in text
    assert '\n  "b c.py" +36' in text


def test_pattern_search_count_only_and_sentence_lists():
    _same(
        "pattern_search",
        {
            "pattern": "x",
            "total": 9,
            "count_only": True,
            "files": [{"file": "a.py", "count": 7}, {"file": "b:c.py", "count": 2}],
            "confidence": "high",
        },
    )
    text = _same(
        "pattern_search",
        {
            "pattern": "where are webhooks retried",
            "total": 1,
            "files": [{"file": "a.py", "hits": [[2, "retry()"]]}],
            "interpreted_as": ["webhooks", "retried"],
            "expanded": ["retry"],
            "symbols": [
                {
                    "kind": "function",
                    "name": "retry",
                    "file": "a.py",
                    "lines": "1-9",
                    "doc": "Retry it.",
                },
                {
                    "scope": "api",
                    "kind": "class",
                    "name": "Hook",
                    "file": "h.py",
                    "lines": "3-30",
                },
            ],
            "docs": [
                {
                    "kind": "md_section",
                    "name": "Retries and backoff",
                    "file": "README.md",
                    "lines": "10",
                }
            ],
            "confidence": "medium",
        },
    )
    assert "\nsymbols:\n" in text
    assert '\n  10 md_section "Retries and backoff"' in text


def test_find_callers_round_trip():
    text = _same(
        "find_callers",
        {
            "fn": "cap_answer",
            "callers": [
                {
                    "caller": "register",
                    "file": "codegraph/server/tools_knowledge.py",
                    "line": 102,
                    "resolution": "resolved",
                },
                {
                    "caller": "_counts_payload",
                    "file": "codegraph/server/tools_query.py",
                    "line": 51,
                    "resolution": "name",
                    "targets": ["a.py", "b.py"],
                },
                {
                    "caller": "run",
                    "file": "codegraph/server/tools_query.py",
                    "line": 262,
                    "resolution": "interface",
                    "via": "Base.run, Proto.run",
                },
                {"caller": "true", "file": "x.py", "line": None, "resolution": "name"},
            ],
            "root": "/work/project",
        },
    )
    assert (
        "\ncodegraph/server/tools_query.py\n  51 _counts_payload name targets=" in text
    )


def test_search_symbols_round_trip():
    text = _same(
        "search_symbols",
        {
            "query": "answer",
            "total": 4,
            "results": [
                {
                    "kind": "function",
                    "name": "answer_bytes",
                    "file": "a.py",
                    "line": 108,
                },
                {
                    "kind": "function",
                    "name": "dumps_answer",
                    "file": "a.py",
                    "line": 103,
                },
                {
                    "kind": "tf_resource",
                    "name": "google_x.y",
                    "type": "google_x",
                    "file": "main.tf",
                    "line": 4,
                },
                {
                    "kind": "md_section",
                    "name": "The answer",
                    "file": "README.md",
                    "line": 1,
                    "level": 2,
                    "anchor": "the-answer",
                },
                {"name": "no_kind", "file": "b.py", "line": 3},
            ],
            "truncated": True,
            "hint": "20 more match(es) past `limit`.",
            "confidence": "high",
        },
    )
    assert "\na.py\n  108 function answer_bytes\n  103 function dumps_answer\n" in text


def test_symbol_lookup_round_trip():
    text = _same(
        "symbol_lookup",
        {
            "found": True,
            "name": "wire_bytes",
            "definitions": [
                {
                    "kind": "function",
                    "name": "wire_bytes",
                    "file": "codegraph/server/_budget.py",
                    "lines": "113-120",
                    "doc": "Bytes an agent receives.",
                    "scope": "parent",
                },
                {
                    "kind": "class",
                    "name": "Wire",
                    "file": "x y.py",
                    "lines": "1-2",
                    "doc": "",
                    "rationale": [{"tag": "WHY", "line": 3, "text": "speed"}],
                    "scope": "parent",
                },
                {
                    "kind": "md_section",
                    "name": "Wire bytes",
                    "file": "README.md",
                    "lines": "4-9",
                    "doc": "multi\nline",
                    "anchor": "wire-bytes",
                    "scope": "parent",
                },
            ],
            "confidence": "high",
        },
    )
    assert (
        "\ncodegraph/server/_budget.py:113-120 function wire_bytes\n  Bytes an agent receives.\n"
        in text
    )
    not_found = {
        "found": False,
        "name": "nope",
        "confidence": "low",
        "why": "No function.",
        "candidates": ["nop"],
    }
    assert _same("symbol_lookup", not_found) == (
        'found=false name=nope confidence=low why="No function." candidates=["nop"]'
    )


def test_federated_answers_get_one_section_per_scope():
    payload = {
        "fn": "run",
        "callers": [
            {
                "caller": "a",
                "file": "main.py",
                "line": 1,
                "resolution": "resolved",
                "scope": "parent",
            },
            {
                "caller": "b",
                "file": "app.py",
                "line": 2,
                "resolution": "name",
                "scope": "api",
            },
            {
                "caller": "c",
                "file": "main.py",
                "line": 9,
                "resolution": "name",
                "scope": "parent",
            },
        ],
        "root": "/work/project",
        "roots": {"api": "/work/project/services/api"},
        "scopes": {"parent": {"commit": "abc"}, "api": {"commit": "def"}},
    }
    text = render_text("find_callers", json.loads(json.dumps(payload)))
    assert text.split("\n")[1:] == [
        "[parent]",
        "main.py",
        "  1 a resolved",
        "  9 c name",
        "[api]",
        "app.py",
        "  2 b name",
    ]
    decoded = decode("find_callers", text)
    by_scope = lambda rows: sorted(rows, key=lambda r: (r["scope"], r["line"]))  # noqa: E731
    assert by_scope(decoded["callers"]) == by_scope(payload["callers"])
    assert decoded["roots"] == payload["roots"]

    hits = render_text(
        "pattern_search",
        {
            "pattern": "x",
            "total": 2,
            "files": [
                {"scope": "parent", "file": "a.py", "hits": [[1, "x"]]},
                {"scope": "web", "file": "a.py", "hits": [[5, "x = 2"]]},
            ],
        },
    )
    assert hits.split("\n")[1:] == [
        "[parent]",
        "a.py",
        "  1: x",
        "[web]",
        "a.py",
        "  5: x = 2",
    ]


# ---------------------------------------------------------------------------
# The ceiling, on the text as received
# ---------------------------------------------------------------------------


def _big_hits(n_files: int, per_file: int, text: str) -> dict:
    return {
        "pattern": "é",
        "total": n_files * per_file,
        "files": [
            {
                "file": f"pkg/módulo_{i}.py",
                "hits": [[j + 1, f"{text} {i}:{j}"] for j in range(per_file)],
            }
            for i in range(n_files)
        ],
        "shown": n_files * per_file,
        "confidence": "high",
    }


@pytest.mark.parametrize(
    "line",
    [
        "café naïve ünïcödé " * 8,
        'quote " and \\ backslash ' * 6,
        "x" * 400,
        "\ttab\nnewline",
    ],
    ids=["non-ascii", "escapes", "long", "control"],
)
@pytest.mark.parametrize("cap", [600, 2500])
def test_text_cap_holds_on_wire_bytes(line, cap):
    raw = json.dumps(_big_hits(20, 6, line), ensure_ascii=False)
    out = text_answer("pattern_search", raw, max_bytes=cap)
    assert wire_bytes(out) <= cap
    assert out.startswith("pattern=")
    header = out.split("\n")[0]
    assert "truncated=true" in header
    assert "dropped=" in header


def test_text_cap_drops_rows_of_every_tool():
    rows = [
        {
            "caller": f"fn_{i}_ünï",
            "file": f"pkg/f{i % 5}.py",
            "line": i,
            "resolution": "name",
        }
        for i in range(400)
    ]
    out = text_answer(
        "find_callers", json.dumps({"fn": "x", "callers": rows}), max_bytes=1500
    )
    assert wire_bytes(out) <= 1500
    kept = decode("find_callers", out)
    assert 0 < len(kept["callers"]) < 400
    assert kept["dropped"] == 400 - len(kept["callers"])


def test_answer_under_the_cap_is_not_cut():
    payload = {
        "query": "q",
        "total": 1,
        "results": [{"kind": "function", "name": "f", "file": "a.py", "line": 1}],
    }
    out = text_answer("search_symbols", json.dumps(payload))
    assert "truncated" not in out
    assert decode("search_symbols", out) == payload


def test_non_object_answers_pass_through():
    assert text_answer("pattern_search", "plain text") == "plain text"
    assert text_answer("pattern_search", "[1, 2]") == "[1, 2]"
    assert text_answer("file_summary", '{"a": 1}') == '{"a": 1}'


# ---------------------------------------------------------------------------
# The setting and the callers
# ---------------------------------------------------------------------------


@pytest.fixture
def no_global_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", home)
    return home


def _write_config(root: Path, value: str) -> None:
    (root / ".codegraph").mkdir(exist_ok=True)
    (root / ".codegraph" / "config.toml").write_text(
        f'[mcp]\nanswer_format = "{value}"\n'
    )


def test_default_is_json(tmp_path, no_global_config):
    assert resolve_format("", tmp_path) == "json"


def test_config_and_env_set_the_default(tmp_path, monkeypatch, no_global_config):
    _write_config(tmp_path, "text")
    assert resolve_format("", tmp_path) == "text"
    assert resolve_format("json", tmp_path) == "json"
    monkeypatch.setenv("CGH_ANSWER_FORMAT", "json")
    assert resolve_format("", tmp_path) == "json"
    assert resolve_format("TEXT", tmp_path) == "text"


def test_global_config_and_unknown_values(tmp_path, monkeypatch, no_global_config):
    (no_global_config / "config.toml").write_text('[mcp]\nanswer_format = "text"\n')
    assert resolve_format("", tmp_path) == "text"
    _write_config(tmp_path, "yaml")
    assert resolve_format("", tmp_path) == "text"
    assert resolve_format("yaml", tmp_path) == "text"
    monkeypatch.setenv("CGH_ANSWER_FORMAT", "nonsense")
    assert resolve_format("", tmp_path) == "text"


def _wrapped(monkeypatch, origin: str, root: Path):
    monkeypatch.setattr(_srv, "_call_origin", lambda: origin)
    monkeypatch.setattr(_srv, "_root", root)
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    def find_callers(fn_name: str, format: str = "") -> str:
        return json.dumps(
            {
                "fn": fn_name,
                "callers": [
                    {
                        "caller": "a",
                        "file": f"{root}/pkg/a.py",
                        "line": 3,
                        "resolution": "name",
                    }
                ],
            }
        )

    return _srv._logged_tool(find_callers)


def test_agent_gets_text_from_the_setting(tmp_path, monkeypatch, no_global_config):
    _write_config(tmp_path, "text")
    out = _wrapped(monkeypatch, "agent", tmp_path)(fn_name="x")
    assert out == f"fn=x root={tmp_path}\npkg/a.py\n  3 a name"
    out = _wrapped(monkeypatch, "agent", tmp_path)(fn_name="x", format="json")
    assert json.loads(out)["callers"][0]["file"] == "pkg/a.py"


def test_agent_asks_for_text_without_the_setting(
    tmp_path, monkeypatch, no_global_config
):
    out = _wrapped(monkeypatch, "agent", tmp_path)(fn_name="x")
    assert json.loads(out)["fn"] == "x"
    out = _wrapped(monkeypatch, "agent", tmp_path)(fn_name="x", format="text")
    assert out.startswith("fn=x ")


@pytest.mark.parametrize("origin", ["cli", "hook", "internal"])
def test_cli_and_hook_callers_stay_on_json(
    tmp_path, monkeypatch, no_global_config, origin
):
    _write_config(tmp_path, "text")
    monkeypatch.setenv("CGH_ANSWER_FORMAT", "text")
    out = _wrapped(monkeypatch, origin, tmp_path)(fn_name="x", format="text")
    assert json.loads(out)["fn"] == "x"


# ---------------------------------------------------------------------------
# Real tools on an indexed repo
# ---------------------------------------------------------------------------


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch, no_global_config):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    reset_connection()
    root = (tmp_path / "repo").resolve()
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        'def target_fn():\n    """Return one, the café way."""\n    return 1\n\n\n'
        "class TargetBox:\n    pass\n"
    )
    (pkg / "main.py").write_text(
        "from pkg.core import target_fn\n\n\ndef run_it():\n"
        '    s = "target_fn\\tquoted"\n    return target_fn()\n\n\n'
        "def run_again():\n    return target_fn()\n"
    )
    index_repo(root, method="os_walk")
    monkeypatch.setattr(_srv, "_root", root)
    monkeypatch.setattr(_srv, "_conn", None)
    monkeypatch.setattr(_srv, "_fts_conn", None)
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    from codegraph.server import _register_query

    m = _FakeMcp()
    _register_query(m)
    yield root, m.tools
    reset_connection()


_CALLS = [
    ("symbol_lookup", {"name": "target_fn"}),
    ("symbol_lookup", {"name": "no_such_name"}),
    ("find_callers", {"fn_name": "target_fn"}),
    ("search_symbols", {"query": "target"}),
    ("pattern_search", {"pattern": "target_fn"}),
    ("pattern_search", {"pattern": "target_fn", "count_only": True}),
]


@pytest.mark.parametrize(
    ("tool", "args"), _CALLS, ids=[f"{c[0]}-{i}" for i, c in enumerate(_CALLS)]
)
def test_real_tool_text_carries_the_json_rows(tools, tool, args):
    _root, registered = tools
    as_json = json.loads(registered[tool](**args, format="json"))
    as_text = registered[tool](**args, format="text")
    assert not as_text.startswith("{")
    assert decode(tool, as_text) == _clean(as_json)
    assert wire_bytes(as_text) < wire_bytes(
        json.dumps(as_json, ensure_ascii=False, separators=(",", ":"))
    )
