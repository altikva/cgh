# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search, search_symbols and symbol_lookup on a sentence
#              (French and English), on an identifier and on a regex; the
#              confidence / why / next fields; definitions ranked before
#              uses; count_only and the narrowing hint of a cut search.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.analysis.pattern import PATTERN_ANSWER_BYTES
from codegraph.analysis.query_intent import clear_centrality_cache
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_query import register as register_query

_FILES = {
    "app/webhooks.py": (
        "def retry_failed_webhooks(limit):\n"
        '    """Relance les webhooks en échec, avec un backoff exponentiel."""\n'
        "    return limit\n"
        "\n"
        "class WebhookDispatcher:\n"
        '    """Send each webhook to its endpoint."""\n'
        "    pass\n"
    ),
    "app/budget.py": (
        "def answer_size(payload):\n"
        '    """Compute the size in bytes of a tool response."""\n'
        "    return len(payload)\n"
    ),
    # Sorts before app/budget.py: its uses come first in grep order.
    "app/a_callers.py": (
        "from app.budget import answer_size\n"
        "\n"
        "def caller_one(p):\n"
        "    return answer_size(p)\n"
        "\n"
        "def caller_two(p):\n"
        "    return answer_size(p) + 1\n"
    ),
}


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
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    reset_connection()
    for rel in _FILES:
        index_file(root / rel, root)
    reset_connection()
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    clear_centrality_cache()
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None
    clear_centrality_cache()


@pytest.fixture
def tools(repo) -> dict:
    m = _FakeMcp()
    register_query(m)
    return m.tools


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


# --- sentences ---------------------------------------------------------------


def test_english_sentence_finds_the_symbol_in_every_tool(tools):
    q = "where are webhooks retried"
    found = json.loads(tools["symbol_lookup"](q))
    assert found["found"] is True
    assert found["definitions"][0]["name"] == "retry_failed_webhooks"
    assert found["interpreted_as"] == ["webhooks", "retried"]
    assert found["confidence"] in ("high", "medium")

    search = json.loads(tools["search_symbols"](q))
    assert "retry_failed_webhooks" in _names(search["results"])
    assert search["interpreted_as"] == ["webhooks", "retried"]

    grep = json.loads(tools["pattern_search"](q))
    assert grep["symbols"][0]["name"] == "retry_failed_webhooks"
    assert grep["files"] == [] and grep["total"] == 0
    assert "expanded" in grep


def test_french_sentence_finds_the_symbol(tools):
    q = "comment est calculée la taille d'une réponse"
    found = json.loads(tools["symbol_lookup"](q))
    assert found["definitions"][0]["name"] == "answer_size"
    assert found["interpreted_as"] == ["calculée", "taille", "réponse"]
    assert {"size", "response"} <= set(found["expanded"])
    assert found["confidence"] == "medium"


def test_identifier_named_in_a_sentence_resolves_exactly(tools):
    out = json.loads(tools["symbol_lookup"]("who calls `answer_size` here?"))
    assert out["definitions"][0]["name"] == "answer_size"
    assert out["confidence"] == "high"
    grep = json.loads(tools["pattern_search"]("who calls `answer_size` here?"))
    # The identifier is grepped too: its definition file comes first.
    assert grep["files"][0]["file"].endswith("app/budget.py")
    assert grep["total"] == 4


def test_sentence_with_nothing_to_find_says_why(tools):
    out = json.loads(tools["symbol_lookup"]("where are the kubernetes manifests"))
    assert out["confidence"] == "low"
    assert out["why"]
    assert out["next"].startswith("pattern_search(")


# --- identifiers and regexes behave as before --------------------------------


def test_identifier_lookup_is_unchanged(tools):
    out = json.loads(tools["symbol_lookup"]("answer_size"))
    assert out["found"] is True
    assert _names(out["definitions"]) == ["answer_size"]
    assert out["confidence"] == "high"
    assert "interpreted_as" not in out and "next" not in out

    search = json.loads(tools["search_symbols"]("answer_size"))
    assert _names(search["results"]) == ["answer_size"]
    assert search["confidence"] == "high"
    assert "interpreted_as" not in search


def test_regex_search_is_unchanged(tools):
    out = json.loads(tools["pattern_search"](r"answer_size\(p\)"))
    assert out["total"] == 2
    assert "interpreted_as" not in out and "symbols" not in out
    assert out["confidence"] == "high"
    assert [g["file"].rsplit("/", 1)[-1] for g in out["files"]] == ["a_callers.py"]


# --- empty answers -----------------------------------------------------------


def test_unknown_identifier_suggests_a_close_name(tools):
    out = json.loads(tools["symbol_lookup"]("answer_sizes"))
    assert out["found"] is False
    assert out["confidence"] == "low"
    assert "answer_sizes" in out["why"]
    assert out["candidates"][0] == "answer_size"
    assert out["next"] == 'symbol_lookup("answer_size")'


def test_empty_search_symbols_says_why_and_what_next(tools):
    out = json.loads(tools["search_symbols"]("zorglub"))
    assert out["total"] == 0
    assert out["confidence"] == "low"
    assert out["why"] and out["next"]


def test_empty_grep_with_glob_suggests_dropping_it(tools):
    out = json.loads(tools["pattern_search"]("answer_size", glob="*.md"))
    assert out["confidence"] == "low"
    assert "*.md" in out["why"]
    assert out["next"] == 'pattern_search("answer_size")'


def test_substring_match_is_medium(tools):
    out = json.loads(tools["search_symbols"]("callers_one"))
    assert out["confidence"] == "low"
    out = json.loads(tools["search_symbols"]("ller_"))
    assert out["confidence"] == "medium"


# --- ranking -----------------------------------------------------------------


def test_definitions_rank_before_uses(tools):
    out = json.loads(tools["pattern_search"]("answer_size"))
    files = [g["file"].rsplit("/", 1)[-1] for g in out["files"]]
    assert files[0] == "budget.py"
    assert "a_callers.py" in files


# --- broad searches ----------------------------------------------------------


@pytest.fixture
def broad(tmp_path):
    root = tmp_path / "broad"
    for d, n in (("pkg/a", 30), ("pkg/b", 12), ("docs", 4)):
        (root / d).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (root / d / f"f{i}.py").write_text(
                "\n".join(f"needle = {j}  # {'x' * 60}" for j in range(3)) + "\n",
                encoding="utf-8",
            )
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    clear_centrality_cache()
    m = _FakeMcp()
    register_query(m)
    yield m.tools
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None


def test_cut_search_names_the_top_directories(broad):
    raw = broad["pattern_search"]("needle", max_results=500)
    out = json.loads(raw)
    assert out["truncated"] is True
    assert len(raw.encode()) <= PATTERN_ANSWER_BYTES + 200
    assert out["top_dirs"][0] == {"glob": "pkg/a/**", "hits": 90}
    assert [d["glob"] for d in out["top_dirs"]] == ["pkg/a/**", "pkg/b/**", "docs/*"]
    assert "pkg/a/** (90)" in out["hint"]
    assert out["confidence"] == "medium"
    assert out["next"] == 'pattern_search("needle", glob="pkg/a/**")'


def test_count_only_returns_counts_per_file(broad):
    raw = broad["pattern_search"]("needle", count_only=True)
    out = json.loads(raw)
    assert out["count_only"] is True
    # count_only looks past the default max_results.
    assert out["total"] == 138
    assert all(set(f) == {"file", "count"} for f in out["files"])
    assert all(f["count"] == 3 for f in out["files"])
    assert out["top_dirs"][0]["glob"] == "pkg/a/**"
    assert len(raw.encode()) <= PATTERN_ANSWER_BYTES
