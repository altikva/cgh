# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: find_callees transitive traversal. max_depth=1 stays the
#              direct callees (backward compatible); max_depth>1 walks the
#              CALLS chain forward and tags each callee with its depth, so
#              a flow trace is one call instead of one lookup per hop.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_query import register as register_query

_CHAIN = (
    "def delta():\n    return 0\n\n\n"
    "def gamma():\n    return delta()\n\n\n"
    "def beta():\n    return gamma()\n\n\n"
    "def alpha():\n    return beta()\n"
)


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def callees(tmp_path):
    reset_connection()
    _srv._root = tmp_path.resolve()
    _srv._conn = None
    src = tmp_path / "chain.py"
    src.write_text(_CHAIN, encoding="utf-8")
    index_file(src, tmp_path.resolve())
    m = _FakeMcp()
    register_query(m)
    yield m.tools["find_callees"]
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _names_by_depth(payload: dict) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for c in payload["callees"]:
        out.setdefault(c["depth"], set()).add(c["callee"])
    return out


def test_default_is_direct_callees_only(callees):
    out = json.loads(callees("alpha"))
    assert out["max_depth"] == 1
    by_depth = _names_by_depth(out)
    assert by_depth == {1: {"beta"}}  # gamma / delta are deeper, not returned


def test_transitive_walks_the_chain(callees):
    out = json.loads(callees("alpha", max_depth=3))
    assert out["max_depth"] == 3
    by_depth = _names_by_depth(out)
    assert by_depth[1] == {"beta"}
    assert by_depth[2] == {"gamma"}
    assert by_depth[3] == {"delta"}


def test_depth_is_clamped_to_the_ceiling(callees):
    out = json.loads(callees("alpha", max_depth=99))
    assert out["max_depth"] == 5  # _CALLEE_DEPTH_CAP, not 99


def test_leaf_has_no_callees(callees):
    out = json.loads(callees("delta", max_depth=3))
    assert out["callees"] == []
