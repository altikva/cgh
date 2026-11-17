# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Symptom search at the MCP layer (L9.4): knowledge_diagnose splits
#              confirmed diagnoses from already-paid false trails, reads the
#              fleet (repo plus subscribed shared stores) by default with dead
#              ends included, marks shared hits untrusted, and knowledge_search
#              gains a symptom mode that surfaces dead ends instead of burying
#              them.

from __future__ import annotations

import json

import pytest

import cgh.server as _srv
from cgh.server.tools_knowledge import register
from cgh.state.call_log import reset_for_tests


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    monkeypatch.setattr(_srv, "_root", root)
    m = _FakeMcp()
    register(m)
    yield m.tools, root
    reset_for_tests()


def test_diagnose_splits_confirmed_from_false_trails(tools):
    t, _ = tools
    good = t["knowledge_record"](
        title="the fix", body="close the RO conn first", kind="gotcha",
        symptom="sqlite3.OperationalError: database is locked (pid 123)",
    )  # fmt: skip
    bad = t["knowledge_record"](
        title="the false trail", body="restarting the owner does nothing",
        kind="gotcha",
        symptom="sqlite3.OperationalError: database is locked (pid 777)",
    )  # fmt: skip
    t["knowledge_outcome"](verdict="useful", entry_id=json.loads(good)["id"])
    t["knowledge_outcome"](verdict="dead_end", entry_id=json.loads(bad)["id"])

    out = json.loads(
        t["knowledge_diagnose"](
            symptom="2026-11-17T09:00:00Z sqlite3.OperationalError: "
            "database is locked (pid 99999)"
        )
    )
    assert {h["title"] for h in out["confirmed"]} == {"the fix"}
    assert {h["title"] for h in out["dead_ends"]} == {"the false trail"}
    dead = out["dead_ends"][0]
    assert dead["verdict"] == "dead_end"
    assert dead.get("already_paid")


def test_diagnose_reads_the_fleet_and_marks_shared_untrusted(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet diagnosis", body="echo of the preflight, docker is fine",
        kind="gotcha", symptom="docker is not installed", namespace="org-platform",
    )  # fmt: skip
    out = json.loads(t["knowledge_diagnose"](symptom="docker is not installed on CI"))
    hit = next(
        h
        for h in out["confirmed"] + out["dead_ends"]
        if h["title"] == "fleet diagnosis"
    )
    assert hit["namespace"] == "org-platform"
    assert hit["untrusted"] is True


def test_diagnose_empty_suggests_recording(tools):
    t, _ = tools
    out = json.loads(t["knowledge_diagnose"](symptom="a brand new never-seen error"))
    assert out["total"] == 0
    assert "suggestion" in out


def test_search_symptom_mode_surfaces_dead_ends(tools):
    t, _ = tools
    bad = t["knowledge_record"](
        title="a known dead end", body="do not go this way", kind="gotcha",
        symptom="ImportError: cannot import name foo from bar",
    )  # fmt: skip
    t["knowledge_outcome"](verdict="dead_end", entry_id=json.loads(bad)["id"])
    out = json.loads(
        t["knowledge_search"](
            query="", symptom="ImportError: cannot import name foo from bar"
        )
    )
    assert out["mode"] == "symptom"
    titles = {h["title"] for h in out["hits"]}
    assert "a known dead end" in titles  # dead end surfaced, not buried
    assert out["hits"][0]["verdict"] == "dead_end"
