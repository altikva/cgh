# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-18
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: search_symbols keeps a federated child in the results when that
#              child's own owner holds the graph write lock, by falling back to
#              the child's FTS index. Pins the behaviour against the earlier
#              version, where such a child came back as a partial scope with
#              zero rows.

from __future__ import annotations

import json

import pytest

import codegraph.analysis.federation as fed
import codegraph.server as _srv
from codegraph.analysis.federation import ScopedResult, add_subrepo
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_query import register as register_query


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _mk_indexed(root, body: str):
    root.mkdir(parents=True, exist_ok=True)
    src = root / "lib.py"
    src.write_text(body, encoding="utf-8")
    reset_connection()
    index_file(src, root)
    reset_connection()
    return root


@pytest.fixture
def federated_parent(tmp_path, monkeypatch):
    """Parent + child, with the child's graph DB reported as locked."""
    parent = _mk_indexed(tmp_path / "parent", "def parent_widget():\n    return 1\n")
    child = _mk_indexed(tmp_path / "childrepo", "def child_widget():\n    return 2\n")
    add_subrepo(parent, child)
    reset_connection()

    def _locked(repo_root, fn):
        return [
            ScopedResult(
                scope="childrepo",
                scope_path=child,
                payload=None,
                error="db unavailable (missing or locked)",
            )
        ]

    monkeypatch.setattr(fed, "for_each_child_graphdb", _locked)
    _srv._root = parent
    _srv._conn = None

    yield parent

    reset_connection()
    _srv._root = None
    _srv._conn = None


def _tools() -> dict:
    m = _FakeMcp()
    register_query(m)
    return m.tools


def test_locked_child_answers_from_fts(federated_parent):
    out = json.loads(_tools()["search_symbols"]("widget"))
    scopes = {r["scope"] for r in out["results"]}

    assert scopes == {"parent", "childrepo"}
    assert not out.get("partial")
    assert not out.get("warnings")


def test_filtered_search_keeps_the_partial_warning(federated_parent):
    """role / layer need the child's File nodes, which FTS cannot provide."""
    out = json.loads(_tools()["search_symbols"]("widget", role="router"))

    assert out.get("partial") is True
    assert [w["scope"] for w in out["warnings"]] == ["childrepo"]
