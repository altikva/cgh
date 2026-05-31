"""Tests for session-scoped dedup in call_log.py."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_call_log(tmp_path, monkeypatch):
    """Force call_log into a fresh tmp dir and reset its cached connection."""
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.chdir(tmp_path)
    import codegraph.state.call_log as cl

    cl._conn = None
    yield tmp_path
    cl._conn = None


class TestSessionDedup:
    def test_filter_unseen_empty_session(self, _reset_call_log):
        from codegraph.state.call_log import filter_unseen

        entities = [("memory", "/m/1"), ("symbol", "/s:42")]
        assert filter_unseen("", entities) == entities  # no session_id → pass-through
        assert filter_unseen("sess-x", entities) == entities  # nothing recorded yet

    def test_record_then_filter_hides(self, _reset_call_log):
        from codegraph.state.call_log import filter_unseen, record_mentions

        entities = [("memory", "/m/1"), ("plan", "/p/x.md"), ("symbol", "/s:1")]
        record_mentions("sess-a", entities)
        assert filter_unseen("sess-a", entities) == []

    def test_clear_session_resets(self, _reset_call_log):
        from codegraph.state.call_log import clear_session, filter_unseen, record_mentions

        e = [("memory", "/m/2")]
        record_mentions("sess-b", e)
        assert filter_unseen("sess-b", e) == []

        removed = clear_session("sess-b")
        assert removed == 1
        assert filter_unseen("sess-b", e) == e

    def test_sessions_are_isolated(self, _reset_call_log):
        from codegraph.state.call_log import filter_unseen, record_mentions

        shared = [("symbol", "/common:7")]
        record_mentions("sess-1", shared)
        # Session 2 shouldn't see session 1's mentions
        assert filter_unseen("sess-2", shared) == shared

    def test_partial_overlap(self, _reset_call_log):
        from codegraph.state.call_log import filter_unseen, record_mentions

        record_mentions("sess-c", [("memory", "/m/known")])
        mixed = [("memory", "/m/known"), ("memory", "/m/new"), ("plan", "/p/new")]
        unseen = filter_unseen("sess-c", mixed)
        assert ("memory", "/m/known") not in unseen
        assert ("memory", "/m/new") in unseen
        assert ("plan", "/p/new") in unseen
