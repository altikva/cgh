# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-22
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: resume answers in two levels: a summary of about 2 KB by
#              default, the whole bundle or one section on demand. Covers the
#              budget on a large store, standing instructions never lost, the
#              goal and next step read from a checkpoint, and the tool routing.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.server.tools_session import (
    SUMMARY_BUDGET,
    digest_brief,
    register,
    resume_section,
    resume_summary,
)
from codegraph.state import call_log
from codegraph.state.call_log import knowledge_record

LONG_RULE = (
    "To find where to edit, use cgh before any shell search: symbol_lookup "
    "for a definition, find_callers for call sites, pattern_search for text. "
) * 6


@pytest.fixture
def store(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    call_log.reset_for_tests()
    knowledge_record(
        "Use the commit skill", "Never raw git commit.",
        kind="standing_instruction", repo_root=tmp_path,
    )  # fmt: skip
    knowledge_record(
        "Locate code with cgh first", LONG_RULE,
        kind="standing_instruction", repo_root=tmp_path,
    )  # fmt: skip
    for i in range(40):
        knowledge_record(
            f"Session digest other-{i}", "Goal: ship it.\n" + "z" * 2000,
            tags="compaction,session-digest", session_id=f"other-{i}",
            repo_root=tmp_path,
        )  # fmt: skip
    for i in range(60):
        knowledge_record(
            f"Gotcha {i} about DuckDB locks", "body " * 80,
            kind="gotcha", repo_root=tmp_path,
        )  # fmt: skip
    knowledge_record(
        "Portal filter", "1. **Goal**: add the portal filter.\n"
        "2. **Done**: the migration.\n"
        "5. **Next step**: wire the filter into the list view.",
        tags="compaction,session-digest", session_id="s1", repo_root=tmp_path,
    )  # fmt: skip
    yield tmp_path
    call_log.reset_for_tests()


def _size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def test_summary_stays_under_budget_on_a_large_store(store):
    out = resume_summary(store, session_id="s1", task="DuckDB locks")
    assert out["mode"] == "summary"
    assert _size(out) <= SUMMARY_BUDGET
    assert out["counts"]["digests"] == 41
    assert out["counts"]["knowledge"] == 60
    assert out["counts"]["standing_instructions"] == 2
    assert "other-" not in json.dumps(out)
    assert "detail=True" in out["more"] and "section=" in out["more"]


def test_summary_carries_goal_and_next_step_of_own_checkpoint(store):
    last = resume_summary(store, session_id="s1")["last_session"]
    assert last["own"] is True
    assert last["goal"] == "add the portal filter."
    assert last["next"] == "wire the filter into the list view."


def test_no_instruction_is_lost(store):
    out = resume_summary(store, session_id="s1")
    titles = [i["title"] for i in out["standing_instructions"]]
    assert titles == ["Locate code with cgh first", "Use the commit skill"]
    short = out["standing_instructions"][1]
    assert short["body"] == "Never raw git commit."
    long = out["standing_instructions"][0]
    if long.get("clipped"):
        assert "section='instructions'" in out["more"]
        assert "before acting" in out["more"]
    else:
        assert long["body"] == " ".join(LONG_RULE.split())


def test_many_long_instructions_are_clipped_and_flagged(store):
    for i in range(8):
        knowledge_record(
            f"Rule {i}", LONG_RULE, kind="standing_instruction", repo_root=store
        )
    out = resume_summary(store)
    assert len(out["standing_instructions"]) == 10
    assert any(i.get("clipped") for i in out["standing_instructions"])
    assert out["more"].startswith(
        f"{sum(1 for i in out['standing_instructions'] if i.get('clipped'))} "
        "standing instruction(s) clipped"
    )
    full = resume_section(store, "instructions")["standing_instructions"]
    assert len(full) == 10
    assert all(e["body"] for e in full)


def test_short_instruction_bodies_fit_in_full(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    call_log.reset_for_tests()
    knowledge_record("Rule", LONG_RULE, kind="standing_instruction", repo_root=tmp_path)
    out = resume_summary(tmp_path)
    assert "clipped" not in out["standing_instructions"][0]
    assert "last_session" not in out
    call_log.reset_for_tests()


def test_sections_return_everything(store):
    assert len(resume_section(store, "digests", session_id="s1")["digests"]) >= 2
    assert (
        resume_section(store, "digests", session_id="s1")["digests"][0]["title"]
        == "Portal filter"
    )
    knowledge = resume_section(store, "knowledge", budget_kb=64)["knowledge"]
    assert len(knowledge) == 8
    assert "body" in knowledge[0]
    assert resume_section(store, "plans")["open_plans"] == []
    assert "error" in resume_section(store, "nope")


def test_digest_brief_labels():
    assert digest_brief("GOAL: ship 0.18.\nNEXT\n- tag it\n- publish\n\nOPEN: x") == {
        "goal": "ship 0.18.",
        "next": "tag it publish",
    }
    assert digest_brief("Objectif : finir.\nProchaine étape : relire.") == {
        "goal": "finir.",
        "next": "relire.",
    }
    assert digest_brief("Next, the portal.") == {"excerpt": "Next, the portal."}


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tool(store):
    m = _FakeMcp()
    register(m)
    _srv._root = store
    yield m.tools["resume"]
    _srv._root = None


def test_tool_default_is_the_summary(tool):
    raw = tool(session_id="s1")
    assert len(raw.encode()) <= SUMMARY_BUDGET
    assert json.loads(raw)["mode"] == "summary"


def test_tool_detail_and_full_return_the_bundle(tool):
    for kwargs in ({"detail": True}, {"full": True}):
        bundle = json.loads(tool(session_id="s1", **kwargs))
        assert "mode" not in bundle
        assert {"standing_instructions", "digests", "knowledge"} <= set(bundle)
        assert len(bundle["knowledge"]) > 0
        assert all("body" in e for e in bundle["standing_instructions"])


def test_tool_section(tool):
    out = json.loads(tool(section="instructions"))
    assert len(out["standing_instructions"]) == 2
