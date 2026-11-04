# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The index-based search tools grep-complete a miss. A name the
#              graph and FTS model no symbol for but that occurs in text
#              (a comment here) comes back as text_matches in one call, so the
#              answer is grep-complete; symbol_lookup, search_symbols and
#              fts_search all do it, and a sentence is never grepped.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.analysis.text_fallback import grep_term
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_meta import register as register_meta
from codegraph.server.tools_query import register as register_query

_BODY = (
    "def parent_widget():\n"
    "    # wiring note: flux_capacitor_7 drives the gearbox here\n"
    "    return 1\n"
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
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".codegraph").mkdir()
    (root / "lib.py").write_text(_BODY, encoding="utf-8")
    reset_connection()
    index_file(root / "lib.py", root)
    reset_connection()
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    m = _FakeMcp()
    register_query(m)
    register_meta(m)
    yield m.tools
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None


def test_grep_term_finds_an_identifier_in_text(repo):
    hits = grep_term(_srv._root, "flux_capacitor_7")
    assert hits and {"scope", "file", "line", "text"} <= set(hits[0])
    assert hits[0]["scope"] == "parent"


def test_grep_term_skips_a_sentence_and_an_absent_name(repo):
    assert grep_term(_srv._root, "how the widget is wired") == []
    assert grep_term(_srv._root, "no_such_token_zz") == []


def test_search_symbols_miss_is_grep_completed(repo):
    out = json.loads(repo["search_symbols"]("flux_capacitor_7"))
    assert out["total"] == 0
    assert out["text_matches"]
    assert out["text_match_source"] == "pattern_search"
    assert out["confidence"] == "medium"


def test_fts_search_miss_is_grep_completed(repo):
    out = json.loads(repo["fts_search"]("flux_capacitor_7"))
    assert not out["results"]
    assert out["text_matches"]
    assert out["confidence"] == "medium"


def test_fts_search_hit_is_high_confidence(repo):
    out = json.loads(repo["fts_search"]("parent_widget"))
    assert out["results"]
    assert out["confidence"] == "high"
    assert "text_matches" not in out


def test_fts_search_absent_name_is_low_and_bare(repo):
    out = json.loads(repo["fts_search"]("no_such_token_zz"))
    assert not out["results"]
    assert out["confidence"] == "low"
    assert "text_matches" not in out
