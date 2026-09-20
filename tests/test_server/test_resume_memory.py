# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-20
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for build_resume_bundle memory reliability: coverage
#              preservation over task routing, digest ranking, and budget
#              enforcement.

from __future__ import annotations

import pytest

from codegraph.server.tools_session import build_resume_bundle
from codegraph.state.call_log import knowledge_record, reset_for_tests


@pytest.fixture
def store(tmp_path):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()

    for i in range(5):
        knowledge_record(
            f"note {i}",
            f"body {i}",
            kind="note",
            tags="cards",
            repo_root=tmp_path,
        )

    knowledge_record(
        "real digest",
        "did X Y Z",
        kind="note",
        tags="compaction,session-digest",
        session_id="s1",
        repo_root=tmp_path,
    )

    for _ in range(2):
        knowledge_record(
            "auto",
            "context event SessionEnd",
            kind="note",
            tags="auto-checkpoint,session-digest",
            repo_root=tmp_path,
        )

    yield tmp_path
    reset_for_tests()


def test_descriptive_task_keeps_coverage(store):
    b = build_resume_bundle(store, task="cards dedup follow-ups zzznomatch")
    assert len(b["knowledge"]) > 0


def test_content_digest_ranks_before_auto(store):
    b = build_resume_bundle(store)
    titles = [d["title"] for d in b["digests"]]
    assert titles[0] == "real digest"
    assert titles.count("auto") <= 1


def test_tiny_budget_reserves_knowledge_and_signals(store):
    b = build_resume_bundle(store, task="", budget_kb=0.5)
    assert len(b["knowledge"]) >= 1
    assert b["truncated"] is True
    assert b["dropped_for_budget"].get("knowledge", 0) >= 1
