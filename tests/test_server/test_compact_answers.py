# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Every registered tool answers compact JSON: no indentation,
#              no space after the separators, accents as is. An indented
#              answer weighed about a fifth more tokens for the same content.
#              Each tool is called on a small indexed repo with arguments
#              built from its signature.

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server._budget import dumps_answer

# Tools that reach the network or rebuild the index in the background.
_SKIP = {"fetch_and_index", "force_index", "scan_repo", "add_directory"}

_ARGS = {
    "file_path": "pkg/core.py",
    "path": "pkg/core.py",
    "file": "pkg/core.py",
    "name": "shared_target",
    "fn_name": "shared_target",
    "symbol_or_file": "shared_target",
    "symbol": "shared_target",
    "query": "shared target",
    "task": "where is the shared target",
    "pattern": "shared_target",
    "keyword": "pkg",
    "title": "Prénom note",
    "body": "Une note « accentuée ».",
    "source": "pkg/core.py",
    "target": "pkg/main.py",
    "digest": "done: nothing",
    "session_id": "s1",
    "url": "https://example.invalid/",
    "changed_files": "pkg/core.py",
    "ref": "HEAD",
}


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _write_repo(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        'def shared_target():\n    """Calcule le total « net »."""\n    return 1\n'
    )
    (pkg / "main.py").write_text(
        "from pkg.core import shared_target\n\n\n"
        "def run():\n    return shared_target()\n"
    )
    (root / "README.md").write_text("# Guide\n\n## Usage\n\nCall run.\n")


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    mp.setenv("CGH_DB", "sqlite")
    reset_connection()
    root = tmp_path_factory.mktemp("compact").resolve()
    _write_repo(root)
    index_repo(root, method="os_walk")
    mp.setattr(_srv, "_root", root)
    mp.setattr(_srv, "_conn", None)
    mp.setattr(_srv, "_fts_conn", None)
    mp.setattr(_srv, "_call_origin", lambda: "agent")
    m = _FakeMcp()
    for name in dir(_srv):
        if name.startswith("_register_"):
            getattr(_srv, name)(m)
    yield m.tools
    reset_connection()
    mp.undo()


def _registered() -> list[str]:
    # FastMCP renamed get_tools (a dict by name) to list_tools; the
    # declared floor still has only the first.
    if hasattr(_srv.mcp, "list_tools"):
        return sorted(t.name for t in asyncio.run(_srv.mcp.list_tools()))
    return sorted(asyncio.run(_srv.mcp.get_tools()))


def _call(fn):
    kwargs = {}
    for p in inspect.signature(fn).parameters.values():
        if p.default is not inspect.Parameter.empty:
            continue
        if p.name in _ARGS:
            kwargs[p.name] = _ARGS[p.name]
        elif p.annotation in (int, "int"):
            kwargs[p.name] = 1
        else:
            kwargs[p.name] = "x"
    return fn(**kwargs)


def test_dumps_answer_is_compact_and_keeps_accents():
    assert dumps_answer({"a": ["é", 1], "b": {"c": None}}) == (
        '{"a":["é",1],"b":{"c":null}}'
    )


@pytest.mark.parametrize("tool", _registered())
def test_tool_answer_is_compact_json(tools, tool):
    if tool in _SKIP:
        pytest.skip("reaches the network or rebuilds in the background")
    out = _call(tools[tool])
    if not isinstance(out, str):
        return
    try:
        json.loads(out)
    except ValueError:
        return  # plain text answers (a diagram, a report) keep their layout
    assert "\n  " not in out, out[:200]
    assert '", "' not in out and '": ' not in out, out[:200]
