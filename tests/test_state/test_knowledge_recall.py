# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Free-text recall in knowledge_search: French and English
#              symptoms find entries worded differently, accents and case are
#              folded, weak matches come back graded low, a zero hit carries a
#              record suggestion, and the recall counters store counts only.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codegraph.state import knowledge_recall as kr
from codegraph.state.call_log import (
    _get_conn,
    get_stats,
    knowledge_record,
    knowledge_search,
    record_recall_outcome,
    reset_for_tests,
)


@pytest.fixture
def store(tmp_path):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    knowledge_record(
        "Stale lock after an owner crash",
        "The owner left .codegraph/owner.lock behind; delete it and restart.",
        kind="gotcha",
        tags="papercut,owner,lock",
        repo_root=tmp_path,
    )
    knowledge_record(
        "gh token expired",
        "Run gh auth refresh when the push fails with a 401.",
        kind="gotcha",
        tags="papercut,github,auth",
        repo_root=tmp_path,
    )
    knowledge_record(
        "Délai dépassé sur le proxy",
        "Le proxy coupe la connexion au bout de 30 s; passer --timeout 120.",
        kind="gotcha",
        tags="papercut,proxy",
        repo_root=tmp_path,
    )
    yield tmp_path
    reset_for_tests()


def _titles(hits: list[dict]) -> list[str]:
    return [h["title"] for h in hits]


# --- matching -------------------------------------------------------------------


def test_french_symptom_finds_an_english_entry(store):
    hits = knowledge_search("le verrou est périmé", repo_root=store, recall=True)
    assert hits[0]["title"] == "Stale lock after an owner crash"
    assert hits[0]["confidence"] == "high"


def test_english_symptom_finds_a_french_entry(store):
    hits = knowledge_search(
        "requests through the proxy hit a timeout", repo_root=store, recall=True
    )
    assert hits[0]["title"] == "Délai dépassé sur le proxy"


def test_stems_match_other_word_forms(store):
    # "locked" and "expires" never appear as written in the store.
    assert "Stale lock after an owner crash" in _titles(
        knowledge_search("database is locked.", repo_root=store, recall=True)
    )
    hits = knowledge_search("jeton expires", repo_root=store, recall=True)
    assert hits[0]["title"] == "gh token expired"


def test_accents_and_case_are_folded(store):
    for query in ("DELAI DEPASSE", "délai dépassé", "Délai Dépassé"):
        hits = knowledge_search(query, repo_root=store, recall=True)
        assert hits[0]["title"] == "Délai dépassé sur le proxy", query
        assert hits[0]["confidence"] == "high"
    assert kr.fold("Périmé ÉCHEC") == "perime echec"


def test_without_recall_the_plain_search_is_unchanged(store):
    assert knowledge_search("le verrou est périmé", repo_root=store) == []
    hits = knowledge_search("stale lock", repo_root=store)
    assert "confidence" not in hits[0]


def test_fts_syntax_still_works_with_recall(store):
    hits = knowledge_search('"auth refresh"', repo_root=store, recall=True)
    assert _titles(hits) == ["gh token expired"]


# --- confidence -----------------------------------------------------------------


def test_weak_match_comes_back_low(store):
    # One concept out of four covered: still returned, graded low.
    hits = knowledge_search(
        "proxy rejects the certificate chain signature", repo_root=store, recall=True
    )
    assert hits
    assert {h["confidence"] for h in hits} == {"low"}


def test_close_spelling_is_a_low_match(store):
    hits = knowledge_search("timout", repo_root=store, recall=True)
    assert hits and hits[0]["confidence"] == "low"


def test_weak_matches_are_capped(store):
    for i in range(6):
        knowledge_record(f"proxy note {i}", "about the proxy", repo_root=store)
    hits = knowledge_search(
        "proxy certificate chain signature mismatch", repo_root=store, recall=True
    )
    assert 0 < len(hits) <= 3


def test_strong_matches_rank_before_weak_ones(store):
    hits = knowledge_search("stale lock owner", repo_root=store, recall=True)
    grades = [kr.CONFIDENCE_ORDER.index(h["confidence"]) for h in hits]
    assert grades == sorted(grades)


def test_confidence_thresholds():
    assert kr.confidence(0, 3) is None
    assert kr.confidence(1, 4) == "low"
    assert kr.confidence(1, 2) == "medium"
    assert kr.confidence(2, 3) == "high"
    assert kr.confidence(3, 3) == "high"


def test_generic_words_do_not_count(store):
    # Every papercut matches "papercut"; that alone is not a match.
    items = kr.concepts("papercut zebra")
    assert kr.coverage(items, "papercut lock") == (0, 1)


def test_body_only_words_weigh_less():
    items = kr.concepts("stale lock")
    assert kr.coverage(items, "stale lock") == (2, 2)
    assert kr.coverage(items, "", "stale lock") == (2 * kr.BODY_WEIGHT, 2)
    assert kr.confidence(*kr.coverage(items, "stale", "lock")) == "high"
    assert kr.confidence(*kr.coverage(items, "", "lock")) == "low"


def test_synonym_table_is_plain_data():
    assert all(isinstance(g, tuple) and g for g in kr.SYNONYMS)
    stems = next(c.stems for c in kr.concepts("jeton"))
    assert "token" in stems


# --- the MCP tool ---------------------------------------------------------------


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tool(store, monkeypatch):
    import codegraph.server as _srv
    from codegraph.server.tools_knowledge import register

    monkeypatch.setattr(_srv, "_root", store)
    m = _FakeMcp()
    register(m)
    return m.tools["knowledge_search"]


def test_tool_answer_keeps_its_fields_and_adds_confidence(tool):
    out = json.loads(tool(query="verrou périmé"))
    assert {"query", "kind", "total", "hits"} <= set(out)
    assert out["confidence"] == "high"
    assert "suggestion" not in out
    assert all("confidence" in h for h in out["hits"])


def test_zero_hit_comes_with_a_record_suggestion(tool):
    out = json.loads(tool(query="papercut uv sync hangs on zscaler"))
    assert out["total"] == 0 and out["hits"] == []
    assert out["confidence"] == "none"
    s = out["suggestion"]
    assert s["tool"] == "knowledge_record"
    assert s["kind"] == "gotcha"
    tags = s["tags"].split(",")
    assert tags[0] == "papercut"
    assert {"uv", "zscaler"} <= set(tags)


def test_suggestion_keeps_the_kind_filter():
    assert kr.suggest_record("zebra stripes", kind="decision")["kind"] == "decision"
    assert kr.suggest_record("zebra stripes")["kind"] == "note"


# --- counters -------------------------------------------------------------------


def test_tool_counts_each_answer_by_confidence(tool, store):
    tool(query="verrou périmé")
    tool(query="papercut zebra quagga okapi")
    tool(query="proxy rejects the certificate chain signature")
    recall = get_stats(store)["knowledge_recall"]
    assert recall["searches"] == 3
    assert recall["by_confidence"] == {"high": 1, "medium": 0, "low": 1, "zero": 1}
    assert recall["zero_hit_rate"] == "33.3%"
    assert recall["low_confidence_rate"] == "33.3%"


def test_counters_store_no_text(store):
    secret = "secret-symptom-xyzzy"
    record_recall_outcome("zero", repo_root=store)
    record_recall_outcome("bogus", repo_root=store)  # ignored
    conn = _get_conn(store)
    rows = conn.execute("SELECT * FROM knowledge_recall_stats").fetchall()
    assert len(rows) == 1
    day, outcome, count = rows[0]
    assert (outcome, count) == ("zero", 1)
    assert len(day) == 10  # YYYY-MM-DD, nothing else
    cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_recall_stats)")]
    assert cols == ["day", "outcome", "count"]
    raw = (store / ".codegraph" / "call_log.db").read_bytes()
    assert secret.encode() not in raw


def test_tool_never_writes_the_query_to_the_counters(tool, store):
    tool(query="xyzzy-plugh-unique-symptom")
    conn = _get_conn(store)
    dump = "\n".join(conn.iterdump())
    stats_lines = [ln for ln in dump.splitlines() if "knowledge_recall_stats" in ln]
    assert stats_lines
    assert not any("xyzzy" in ln for ln in stats_lines)


def test_stats_without_any_search(tmp_path):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    recall = get_stats(tmp_path)["knowledge_recall"]
    assert recall["searches"] == 0
    assert recall["zero_hit_rate"] == "0%"
    reset_for_tests()


def test_stats_table_renders(store):
    from codegraph.cli.commands_monitor import _recall_table

    assert _recall_table({}) is None
    record_recall_outcome("high", repo_root=store)
    table = _recall_table(get_stats(store)["knowledge_recall"])
    assert table is not None and table.row_count == 7


def test_old_store_gets_the_counter_table(tmp_path):
    reset_for_tests()
    db = Path(tmp_path) / ".codegraph" / "call_log.db"
    db.parent.mkdir()
    sqlite3.connect(db).close()
    record_recall_outcome("low", repo_root=tmp_path)
    assert get_stats(tmp_path)["knowledge_recall"]["by_confidence"]["low"] == 1
    reset_for_tests()


# --- answer size ----------------------------------------------------------------


def _bulky(store, n: int = 8) -> None:
    for i in range(n):
        body = "\n".join(
            f"line {j} about the stale lock in the owner" for j in range(80)
        )
        knowledge_record(
            f"Stale lock case {i}",
            body,
            kind="gotcha",
            tags="papercut,lock,stale,owner",
            repo_root=store,
        )


def test_first_hit_keeps_its_body_others_are_briefs(tool, store):
    _bulky(store)
    out = json.loads(tool(query="stale lock owner", limit=20))
    first, rest = out["hits"][0], out["hits"][1:]
    assert len(first["body"]) <= kr.FIRST_BODY + len(" [...]")
    assert first["body_clipped"] is True
    assert "file_refs" in first and "score" in first
    assert rest
    for h in rest:
        assert set(h) <= {
            "id", "title", "kind", "tags", "confidence", "body",
            "body_clipped", "outcome", "scope",
        }  # fmt: skip
        assert len(h.get("body", "")) <= kr.BRIEF_BODY + len(" [...]")
    assert "knowledge_list(entry_id=" in out["read_more"]


def test_default_answer_stays_under_the_target(tool, store):
    from codegraph.server._budget import answer_bytes
    from codegraph.server.tools_knowledge import SEARCH_TARGET_BYTES

    _bulky(store)
    raw = tool(query="stale lock owner")
    out = json.loads(raw)
    assert len(raw.encode("utf-8")) <= SEARCH_TARGET_BYTES
    assert answer_bytes(out) <= SEARCH_TARGET_BYTES
    assert 1 <= len(out["hits"]) <= kr.DEFAULT_LIMIT
    assert out["hits"][0]["title"].startswith("Stale lock")


def test_default_limit_is_five(store):
    import inspect

    from codegraph.server.tools_knowledge import register

    m = _FakeMcp()
    register(m)
    params = inspect.signature(m.tools["knowledge_search"]).parameters
    assert params["limit"].default == kr.DEFAULT_LIMIT == 5


def test_clip_cuts_at_a_line_end():
    text = "first line\n" + "x" * 50 + "\nthird line"
    head, clipped = kr.clip(text, 40)
    # No break in the second half: a hard cut at the limit.
    assert clipped and head == text[:40] + " [...]"
    assert kr.clip("short", 40) == ("short", False)
    head, _ = kr.clip("a" * 30 + "\n" + "b" * 30, 50)
    assert head == "a" * 30 + " [...]"


def test_a_brief_hit_is_read_whole_by_id(store, monkeypatch):
    import codegraph.server as _srv
    from codegraph.server.tools_knowledge import register

    _bulky(store, 3)
    monkeypatch.setattr(_srv, "_root", store)
    m = _FakeMcp()
    register(m)
    out = json.loads(m.tools["knowledge_search"](query="stale lock owner"))
    brief = next(h for h in out["hits"][1:] if "case" in h["title"])
    whole = json.loads(m.tools["knowledge_list"](entry_id=brief["id"]))
    assert whole["returned"] == 1
    entry = whole["entries"][0]
    assert entry["id"] == brief["id"]
    assert entry["body"].count("\n") == 79
    missing = json.loads(m.tools["knowledge_list"](entry_id=99999))
    assert "error" in missing
