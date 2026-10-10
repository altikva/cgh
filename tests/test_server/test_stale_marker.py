# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Graph tools answering from an index this cgh cannot fully trust
#              (older graph format, a store copied from another checkout) say
#              so with `stale: true` and a reason, so an empty answer is not
#              taken for "no callers". A current index carries no marker, and
#              tools outside the graph are left alone.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server.tools_query import register as register_query
from codegraph.state.scan_meta import _meta_path


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    root = tmp_path.resolve()
    (root / "mod.py").write_text(
        "def target():\n    return 1\n\n\ndef caller():\n    return target()\n"
    )
    index_repo(root, method="os_walk")
    _srv._root = root
    m = _FakeMcp()
    register_query(m)
    yield root, m.tools
    reset_connection()
    _srv._root = None


def _edit_meta(repo, **changes):
    path = _meta_path(repo)
    meta = json.loads(path.read_text())
    meta.update(changes)
    path.write_text(json.dumps(meta))


def test_current_index_has_no_marker(tools):
    _root, t = tools
    out = json.loads(t["find_callers"]("target"))
    assert "stale" not in out
    assert [c["caller"] for c in out["callers"]] == ["caller"]


def test_older_graph_format_is_marked(tools):
    root, t = tools
    _edit_meta(root, graph_format=1)
    out = json.loads(t["find_callers"]("target"))
    assert out["stale"] is True
    assert "graph format 1" in out["stale_reason"]
    assert "cgh index" in out["stale_reason"]


def test_copied_store_is_marked(tools):
    root, t = tools
    _edit_meta(root, root="/somewhere/else")
    out = json.loads(t["symbol_lookup"]("target"))
    assert out["stale"] is True
    assert "/somewhere/else" in out["stale_reason"]


def test_mark_stale_leaves_non_objects_alone():
    assert _srv._mark_stale("[1, 2]", "why") == "[1, 2]"
    assert _srv._mark_stale("plain text", "why") == "plain text"
    assert _srv._mark_stale('{"a": 1}', "") == '{"a": 1}'
    assert json.loads(_srv._mark_stale('{"a": 1}', "why")) == {
        "a": 1,
        "stale": True,
        "stale_reason": "why",
    }
