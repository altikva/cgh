# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The `decide` MCP tool against a fixture store: the exact key set
#              of an answer (recommended, nothing recommended, empty store), a
#              useful precedent recommended, a dead end reported against, the
#              fix of a corrected entry pulled in, digests and notes dropped,
#              a shared hit never recommended, and nothing written.

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cgh.state.call_log import (
    _get_conn,
    knowledge_outcome_record,
    knowledge_record,
    reset_for_tests,
)

_ALWAYS = {
    "question",
    "recommendation",
    "confidence",
    "basis",
    "alternatives",
    "evidence",
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
def repo(tmp_path, monkeypatch) -> Iterator[Path]:
    """An empty repo store, with GLOBAL_DIR redirected so no shared store of
    the machine is read."""
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    yield root
    reset_for_tests()


@pytest.fixture
def decide(repo, monkeypatch):
    import cgh.server as _srv
    from cgh.server.tools_decision import register

    monkeypatch.setattr(_srv, "_root", repo)
    m = _FakeMcp()
    register(m)

    def call(**kwargs) -> dict:
        return json.loads(m.tools["decide"](**kwargs))

    return call


def _squash(repo: Path) -> int:
    return knowledge_record(
        "Squash merge feature branches into develop",
        "Feature branches are squash merged into develop so the history stays "
        "one commit per ticket. Release branches keep a merge commit.",
        kind="decision",
        tags="git,merge,develop",
        repo_root=repo,
    )


# --- answer shape ---------------------------------------------------------------


def test_recommended_answer_has_exactly_these_keys(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(verdict="useful", entry_id=eid, repo_root=repo)
    out = decide(question="squash merge into develop")
    assert set(out) == _ALWAYS | {"next"}
    assert set(out["evidence"]) == {"supporting", "against", "untrusted"}
    assert set(out["recommendation"]) == {
        "entry_id",
        "title",
        "kind",
        "statement",
        "recorded",
        "outcome",
    }
    assert set(out["evidence"]["supporting"][0]) == {
        "id",
        "title",
        "kind",
        "match",
        "outcome",
    }


def test_answer_without_recommendation_has_exactly_these_keys(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(
        verdict="dead_end", entry_id=eid, note="lost the bisect", repo_root=repo
    )
    out = decide(question="squash merge into develop")
    assert set(out) == _ALWAYS | {"suggestion"}
    assert out["recommendation"] is None
    assert set(out["evidence"]["against"][0]) == {
        "id",
        "title",
        "kind",
        "match",
        "outcome",
        "excerpt",
    }


def test_empty_store_answers_none_with_exactly_these_keys(decide):
    out = decide(question="which queue do we use for webhooks")
    assert set(out) == _ALWAYS | {"suggestion"}
    assert out["recommendation"] is None
    assert out["confidence"] == "none"
    assert out["basis"] == "no precedent found"
    assert out["alternatives"] == []
    assert out["evidence"] == {"supporting": [], "against": [], "untrusted": []}
    assert out["suggestion"]["tool"] == "knowledge_record"
    assert out["suggestion"]["kind"] == "decision"


def test_empty_question_is_an_error_not_an_answer(decide):
    out = decide(question="   ")
    assert set(out) == {"error"}


# --- the three fixture cases of the ticket --------------------------------------


def test_a_useful_precedent_is_recommended_with_high_confidence(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(verdict="useful", entry_id=eid, repo_root=repo)
    out = decide(question="squash merge into develop")
    rec = out["recommendation"]
    assert rec["entry_id"] == eid
    assert rec["kind"] == "decision"
    assert rec["statement"].startswith("Feature branches are squash merged")
    assert rec["outcome"]["status"] == "useful"
    assert out["confidence"] == "high"
    assert out["basis"] == "match high, outcome useful, no rival"
    assert f"knowledge_outcome(entry_id={eid}" in out["next"]


def test_an_unrated_precedent_is_recommended_at_medium(decide, repo):
    eid = _squash(repo)
    out = decide(question="squash merge into develop")
    assert out["recommendation"]["entry_id"] == eid
    assert "outcome" not in out["recommendation"]
    assert out["confidence"] == "medium"


def test_a_dead_end_is_never_recommended_and_always_shown(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(
        verdict="dead_end", entry_id=eid, note="lost the bisect", repo_root=repo
    )
    out = decide(question="squash merge into develop")
    assert out["recommendation"] is None
    assert out["confidence"] == "low"
    assert out["evidence"]["supporting"] == []
    (against,) = out["evidence"]["against"]
    assert against["id"] == eid
    assert against["outcome"] == {"status": "dead_end", "last_note": "lost the bisect"}


def test_a_dead_end_stays_visible_next_to_the_precedent_that_holds(decide, repo):
    bad = _squash(repo)
    good = knowledge_record(
        "Merge commit for feature branches into develop",
        "Feature branches merge into develop with a merge commit; squash "
        "merge lost the bisect history.",
        kind="decision",
        tags="git,merge,develop",
        repo_root=repo,
    )
    knowledge_outcome_record(verdict="dead_end", entry_id=bad, repo_root=repo)
    knowledge_outcome_record(verdict="useful", entry_id=good, repo_root=repo)
    out = decide(question="squash merge into develop")
    assert out["recommendation"]["entry_id"] == good
    assert [e["id"] for e in out["evidence"]["against"]] == [bad]


# --- candidates -----------------------------------------------------------------


def test_the_fix_of_a_corrected_entry_is_pulled_in(decide, repo):
    wrong = _squash(repo)
    # Worded so the question does not find it on its own.
    fix = knowledge_record(
        "History policy for integration",
        "Keep one parent per ticket only on release lines.",
        kind="decision",
        tags="policy",
        repo_root=repo,
    )
    knowledge_outcome_record(
        verdict="corrected", entry_id=wrong, corrected_by=fix, repo_root=repo
    )
    out = decide(question="squash merge into develop")
    (against,) = out["evidence"]["against"]
    assert against["id"] == wrong and against["corrected_by"] == fix
    assert out["recommendation"]["entry_id"] == fix
    # The fix answers the question as well as the entry it replaces.
    assert out["evidence"]["supporting"][0]["match"] == "high"


def test_digests_notes_and_glossary_are_not_precedents(decide, repo):
    for kind, tags in (
        ("note", "git"),
        ("glossary", "git"),
        ("decision", "git,session-digest"),
    ):
        knowledge_record(
            "Squash merge into develop",
            "squash merge into develop",
            kind=kind,
            tags=tags,
            repo_root=repo,
        )
    out = decide(question="squash merge into develop")
    assert out["confidence"] == "none"
    assert out["evidence"] == {"supporting": [], "against": [], "untrusted": []}


def test_a_standing_instruction_outranks_a_useful_decision(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(verdict="useful", entry_id=eid, repo_root=repo)
    rule = knowledge_record(
        "Never squash merge into develop",
        "Squash merge into develop is forbidden: always a merge commit.",
        kind="standing_instruction",
        tags="git,merge,develop",
        repo_root=repo,
    )
    out = decide(question="squash merge into develop")
    assert out["recommendation"]["entry_id"] == rule
    assert [e["id"] for e in out["evidence"]["supporting"]] == [rule, eid]


# --- shared stores --------------------------------------------------------------


def _subscribe(repo: Path, name: str) -> None:
    (repo / ".cgh" / "config.toml").write_text(
        f'[cgh]\nknowledge_namespaces = ["{name}"]\n', encoding="utf-8"
    )


def _shared_entry(repo: Path) -> None:
    knowledge_record(
        "Squash everywhere",
        "The fleet uses squash for every branch.",
        kind="decision",
        repo_root=repo,
        namespace="org-platform",
        origin_repo="media-service",
    )


def test_a_shared_hit_is_reported_untrusted_and_never_recommended(decide, repo):
    _subscribe(repo, "org-platform")
    _shared_entry(repo)
    out = decide(question="squash")
    assert out["recommendation"] is None
    assert out["confidence"] == "low"
    (shared,) = out["evidence"]["untrusted"]
    assert shared["id"].startswith("org-platform:")
    assert shared["namespace"] == "org-platform"
    assert shared["origin_repo"] == "media-service"
    assert out["evidence"]["supporting"] == []


def test_a_shared_hit_does_not_raise_a_local_confidence(decide, repo):
    _subscribe(repo, "org-platform")
    _shared_entry(repo)
    eid = knowledge_record(
        "Squash",
        "squash on this repo",
        kind="decision",
        repo_root=repo,
    )
    out = decide(question="squash")
    assert out["recommendation"]["entry_id"] == eid
    assert out["confidence"] == "medium"
    assert "contested" not in out
    assert len(out["evidence"]["untrusted"]) == 1


def test_a_local_namespace_does_not_read_the_shared_stores(decide, repo):
    _subscribe(repo, "org-platform")
    _shared_entry(repo)
    out = decide(question="squash", namespace="repo")
    assert out["confidence"] == "none"
    assert out["evidence"]["untrusted"] == []


def test_a_shared_namespace_reads_that_store_only_and_recommends_nothing(decide, repo):
    _shared_entry(repo)
    knowledge_record("Squash", "squash here", kind="decision", repo_root=repo)
    out = decide(question="squash", namespace="org-platform")
    assert out["recommendation"] is None
    assert out["evidence"]["supporting"] == []
    assert len(out["evidence"]["untrusted"]) == 1


# --- read only, bounded ---------------------------------------------------------


def test_decide_writes_nothing_to_the_store(decide, repo):
    eid = _squash(repo)
    knowledge_outcome_record(verdict="useful", entry_id=eid, repo_root=repo)
    conn = _get_conn(repo)

    def counts() -> tuple:
        return tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("knowledge", "knowledge_outcome")
        )

    before = counts()
    decide(question="squash merge into develop", session_id="s-1")
    assert counts() == before


def test_the_answer_stays_within_the_search_budget(decide, repo):
    from cgh.server._budget import answer_bytes
    from cgh.server.tools_knowledge import SEARCH_TARGET_BYTES

    for i in range(12):
        knowledge_record(
            f"Squash merge into develop, variant {i}",
            ("squash merge into develop. " + "detail " * 300),
            kind="decision",
            tags="git,merge,develop",
            repo_root=repo,
        )
    out = decide(question="squash merge into develop", limit=10)
    assert out["recommendation"] is not None
    assert answer_bytes(out) <= SEARCH_TARGET_BYTES
