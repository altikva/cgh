"""Tests for the knowledge store in call_log.py."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_call_log(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.chdir(tmp_path)
    import codegraph.state.call_log as cl

    cl._conn = None
    yield tmp_path
    cl._conn = None


class TestKnowledgeRecord:
    def test_record_and_search(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_record, knowledge_search

        eid = knowledge_record(
            title="Avoid Kuzu double-open",
            body="Kuzu holds an OS lock; release via reset_connection() before opening again.",
            kind="gotcha",
            tags=["kuzu", "db", "lock"],
        )
        assert eid > 0

        hits = knowledge_search("Kuzu lock")
        assert hits
        assert hits[0]["title"] == "Avoid Kuzu double-open"
        assert hits[0]["kind"] == "gotcha"
        assert "kuzu" in hits[0]["tags"]

    def test_kind_filter(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_record, knowledge_search

        knowledge_record("Pattern A", "description", kind="pattern", tags="routing")
        knowledge_record("Decision A", "description", kind="decision", tags="routing")

        patterns = knowledge_search("routing", kind="pattern")
        assert len(patterns) == 1
        assert patterns[0]["kind"] == "pattern"

    def test_list_by_tag(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_list, knowledge_record

        knowledge_record("A", "x", tags="alpha,beta")
        knowledge_record("B", "y", tags="beta,gamma")
        knowledge_record("C", "z", tags="delta")

        beta = knowledge_list(tag="beta")
        assert len(beta) == 2
        assert {e["title"] for e in beta} == {"A", "B"}

    def test_list_by_session(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_list, knowledge_record

        knowledge_record("S1 note", "x", session_id="sess-1")
        knowledge_record("S2 note", "y", session_id="sess-2")

        s1 = knowledge_list(session_id="sess-1")
        assert len(s1) == 1
        assert s1[0]["title"] == "S1 note"

    def test_invalid_kind_falls_back_to_note(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_list, knowledge_record

        knowledge_record("X", "y", kind="weird-kind")
        entries = knowledge_list()
        assert entries[0]["kind"] == "note"


class TestGlossary:
    def test_terms_aggregation(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_record, knowledge_terms

        knowledge_record("A", "x", tags=["kuzu", "db"])
        knowledge_record("B", "y", tags=["kuzu", "lock"])
        knowledge_record("C", "z", tags=["kuzu"])

        terms = dict(knowledge_terms())
        assert terms["kuzu"] == 3
        assert terms["db"] == 1

    def test_min_count_filter(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_record, knowledge_terms

        knowledge_record("A", "x", tags=["a", "b"])
        knowledge_record("B", "y", tags=["b", "c"])

        terms = dict(knowledge_terms(min_count=2))
        assert terms == {"b": 2}


class TestForget:
    def test_forget_removes(self, _fresh_call_log):
        from codegraph.state.call_log import (
            knowledge_forget,
            knowledge_list,
            knowledge_record,
        )

        eid = knowledge_record("X", "y")
        assert len(knowledge_list()) == 1
        assert knowledge_forget(eid) is True
        assert len(knowledge_list()) == 0

    def test_forget_missing_returns_false(self, _fresh_call_log):
        from codegraph.state.call_log import knowledge_forget

        assert knowledge_forget(9999) is False
