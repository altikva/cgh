"""Tests for the knowledge store in call_log.py."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_call_log(tmp_path, monkeypatch):
    (tmp_path / ".cgh").mkdir()
    monkeypatch.chdir(tmp_path)
    import cgh.state.call_log as cl

    cl._conn = None
    yield tmp_path
    cl._conn = None


class TestKnowledgeRecord:
    def test_record_and_search(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_record, knowledge_search

        eid = knowledge_record(
            title="Avoid DuckDB double-open",
            body="DuckDB rejects a RO open while a RW conn is live; reset_connection() before reopening.",
            kind="gotcha",
            tags=["duckdb", "db", "lock"],
        )
        assert eid > 0

        hits = knowledge_search("DuckDB lock")
        assert hits
        assert hits[0]["title"] == "Avoid DuckDB double-open"
        assert hits[0]["kind"] == "gotcha"
        assert "duckdb" in hits[0]["tags"]

    def test_kind_filter(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_record, knowledge_search

        knowledge_record("Pattern A", "description", kind="pattern", tags="routing")
        knowledge_record("Decision A", "description", kind="decision", tags="routing")

        patterns = knowledge_search("routing", kind="pattern")
        assert len(patterns) == 1
        assert patterns[0]["kind"] == "pattern"

    def test_list_by_tag(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_list, knowledge_record

        knowledge_record("A", "x", tags="alpha,beta")
        knowledge_record("B", "y", tags="beta,gamma")
        knowledge_record("C", "z", tags="delta")

        beta = knowledge_list(tag="beta")
        assert len(beta) == 2
        assert {e["title"] for e in beta} == {"A", "B"}

    def test_list_by_session(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_list, knowledge_record

        knowledge_record("S1 note", "x", session_id="sess-1")
        knowledge_record("S2 note", "y", session_id="sess-2")

        s1 = knowledge_list(session_id="sess-1")
        assert len(s1) == 1
        assert s1[0]["title"] == "S1 note"

    def test_invalid_kind_falls_back_to_note(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_list, knowledge_record

        knowledge_record("X", "y", kind="weird-kind")
        entries = knowledge_list()
        assert entries[0]["kind"] == "note"


class TestGlossary:
    def test_terms_aggregation(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_record, knowledge_terms

        knowledge_record("A", "x", tags=["duckdb", "db"])
        knowledge_record("B", "y", tags=["duckdb", "lock"])
        knowledge_record("C", "z", tags=["duckdb"])

        terms = dict(knowledge_terms())
        assert terms["duckdb"] == 3
        assert terms["db"] == 1

    def test_min_count_filter(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_record, knowledge_terms

        knowledge_record("A", "x", tags=["a", "b"])
        knowledge_record("B", "y", tags=["b", "c"])

        terms = dict(knowledge_terms(min_count=2))
        assert terms == {"b": 2}


class TestForget:
    def test_forget_removes(self, _fresh_call_log):
        from cgh.state.call_log import (
            knowledge_forget,
            knowledge_list,
            knowledge_record,
        )

        eid = knowledge_record("X", "y")
        assert len(knowledge_list()) == 1
        assert knowledge_forget(eid) is True
        assert len(knowledge_list()) == 0

    def test_forget_missing_returns_false(self, _fresh_call_log):
        from cgh.state.call_log import knowledge_forget

        assert knowledge_forget(9999) is False


def _age_entry(store, entry_id: int, days: float) -> None:
    """Push a knowledge row's own timestamps back, so recency decay sees it as
    old without touching any outcome verdict's freshness."""
    import time

    import cgh.state.call_log as cl

    conn = cl._get_conn(store)
    old = time.time() - days * 86400.0
    conn.execute(
        "UPDATE knowledge SET ts = ?, origin_ts = ? WHERE id = ?",
        (old, old, entry_id),
    )
    conn.commit()


def _superseded_by(store, entry_id: int):
    import cgh.state.call_log as cl

    row = (
        cl._get_conn(store)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (entry_id,))
        .fetchone()
    )
    return row[0] if row else None


class TestRecencyDecay:
    def test_off_by_default_keeps_old_notes(self, _fresh_call_log):
        # No config: age decay is off, so an old note is not demoted for age.
        from cgh.state.call_log import knowledge_record, knowledge_search

        old = knowledge_record("Old", "overflow glitch", kind="gotcha", tags="overflow")
        _age_entry(_fresh_call_log, old, 300)
        knowledge_record("New", "overflow glitch", kind="gotcha", tags="overflow")
        hits = knowledge_search("overflow glitch")
        assert {h["title"] for h in hits} == {"Old", "New"}
        # Equal relevance, decay off: the first-recorded (old) one is not sunk.
        assert hits[0]["title"] == "Old"

    def test_enabled_sinks_old_notes(self, _fresh_call_log):
        store = _fresh_call_log
        (store / ".cgh" / "config.toml").write_text(
            "[cgh]\nknowledge_recency_half_life_days = 15\n", encoding="utf-8"
        )
        from cgh.state.call_log import knowledge_record, knowledge_search

        old = knowledge_record("Old", "overflow glitch", kind="gotcha", tags="overflow")
        _age_entry(store, old, 150)  # ten half-lives: all but buried
        knowledge_record("New", "overflow glitch", kind="gotcha", tags="overflow")
        hits = knowledge_search("overflow glitch", repo_root=store)
        assert hits[0]["title"] == "New"
        assert "Old" in {h["title"] for h in hits}

    def test_spares_standing_instruction_and_glossary(self, _fresh_call_log):
        store = _fresh_call_log
        (store / ".cgh" / "config.toml").write_text(
            "[cgh]\nknowledge_recency_half_life_days = 10\n", encoding="utf-8"
        )
        from cgh.state.call_log import knowledge_record, knowledge_search

        si = knowledge_record(
            "Rule", "overflow rule", kind="standing_instruction", tags="overflow"
        )
        gl = knowledge_record("Term", "overflow rule", kind="glossary", tags="overflow")
        gt = knowledge_record("Bug", "overflow rule", kind="gotcha", tags="overflow")
        for eid in (si, gl, gt):
            _age_entry(store, eid, 120)  # all equally old
        hits = knowledge_search("overflow rule", repo_root=store)
        titles = [h["title"] for h in hits]
        # The durable conventions keep their rank; the aged gotcha sinks last.
        assert titles.index("Bug") == len(titles) - 1
        assert set(titles[:2]) == {"Rule", "Term"}

    def test_spares_notes_still_rated_useful(self, _fresh_call_log):
        store = _fresh_call_log
        (store / ".cgh" / "config.toml").write_text(
            "[cgh]\nknowledge_recency_half_life_days = 10\n", encoding="utf-8"
        )
        from cgh.state.call_log import (
            knowledge_outcome_record,
            knowledge_record,
            knowledge_search,
        )

        good = knowledge_record(
            "Proven", "overflow fix", kind="gotcha", tags="overflow"
        )
        knowledge_outcome_record("useful", entry_id=good, repo_root=store)
        cold = knowledge_record("Stale", "overflow fix", kind="gotcha", tags="overflow")
        # Age both notes, but leave the fresh useful verdict on `good` untouched.
        _age_entry(store, good, 120)
        _age_entry(store, cold, 120)
        hits = knowledge_search("overflow fix", repo_root=store)
        assert hits[0]["title"] == "Proven"


class TestAutoSupersede:
    def test_explicit_contradicts_with_tags_auto_supersedes(self, _fresh_call_log):
        store = _fresh_call_log
        from cgh.state.call_log import knowledge_record, knowledge_search

        old = knowledge_record(
            "RO opens block", "old lock rule", kind="gotcha", tags="duckdb,lock,ro"
        )
        new = knowledge_record(
            "RO opens are fine now",
            "en fait, le lock ne bloque plus",
            kind="gotcha",
            tags="duckdb,lock,ro",
            contradicts=old,
        )
        assert _superseded_by(store, old) == new
        # A superseded note drops out of search.
        assert old not in {h["id"] for h in knowledge_search("lock", repo_root=store)}

    def test_corrected_outcome_with_tags_auto_supersedes(self, _fresh_call_log):
        store = _fresh_call_log
        from cgh.state.call_log import knowledge_outcome_record, knowledge_record

        old = knowledge_record(
            "Retry on 500", "retry webhooks", kind="gotcha", tags="webhook,retry"
        )
        knowledge_outcome_record("corrected", entry_id=old, repo_root=store)
        new = knowledge_record(
            "Do not retry",
            "retry webhooks differently",
            kind="gotcha",
            tags="webhook,retry",
        )
        assert _superseded_by(store, old) == new

    def test_tags_only_is_a_suggestion_not_auto(self, _fresh_call_log):
        store = _fresh_call_log
        from cgh.state.call_log import knowledge_record, supersede_candidates

        old = knowledge_record(
            "Alpha rule", "body", kind="gotcha", tags="alpha,beta,gamma"
        )
        cand = supersede_candidates(
            store, kind="gotcha", tags="alpha,beta,gamma", title="New", body="body"
        )
        assert cand["strong"] == []
        assert [c["id"] for c in cand["weak"]] == [old]
        # Recording a tag-twin without an explicit signal leaves the old live.
        knowledge_record(
            "Alpha rule v2", "body", kind="gotcha", tags="alpha,beta,gamma"
        )
        assert _superseded_by(store, old) is None

    def test_low_tag_overlap_is_no_candidate(self, _fresh_call_log):
        store = _fresh_call_log
        from cgh.state.call_log import knowledge_record, supersede_candidates

        knowledge_record(
            "Alpha", "b", kind="gotcha", tags="alpha,beta,gamma,delta,epsilon"
        )
        cand = supersede_candidates(store, kind="gotcha", tags="alpha,zeta", title="x")
        assert cand["strong"] == [] and cand["weak"] == []

    def test_different_kind_is_not_a_candidate(self, _fresh_call_log):
        store = _fresh_call_log
        from cgh.state.call_log import knowledge_record, supersede_candidates

        knowledge_record("Deco", "b", kind="decision", tags="duckdb,lock,ro")
        cand = supersede_candidates(
            store, kind="gotcha", tags="duckdb,lock,ro", title="x"
        )
        assert cand["strong"] == [] and cand["weak"] == []
