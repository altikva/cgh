# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh decide`: --json prints the MCP payload, the text output
#              names the recommendation, the confidence and the three evidence
#              lists, the exit code is 0 whatever the confidence and 1 on
#              error (no cgh project, empty question).

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path

import pytest

from cgh.cli.commands_decision import cmd_decide, register_decision_parser
from cgh.state.call_log import (
    knowledge_outcome_record,
    knowledge_record,
    reset_for_tests,
)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Iterator[Path]:
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    yield root
    reset_for_tests()


def _args(root: Path, question: str, **over) -> Namespace:
    base = {
        "question": question,
        "root": str(root),
        "namespace": "",
        "limit": 5,
        "json": False,
    }
    return Namespace(**(base | over))


def _precedent(root: Path, verdict: str = "") -> int:
    eid = knowledge_record(
        "Squash merge feature branches into develop",
        "Feature branches are squash merged into develop.",
        kind="decision",
        tags="git,merge,develop",
        repo_root=root,
    )
    if verdict:
        knowledge_outcome_record(
            verdict=verdict, entry_id=eid, note="seen on the release", repo_root=root
        )
    return eid


def test_parser_takes_the_question_and_the_documented_flags():
    import argparse

    parser = argparse.ArgumentParser()
    register_decision_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(
        ["decide", "which merge", "--namespace", "repo", "--limit", "3", "--json"]
    )
    assert (args.cmd, args.question) == ("decide", "which merge")
    assert (args.namespace, args.limit, args.json) == ("repo", 3, True)


def test_json_prints_the_tool_payload(repo, capsys):
    eid = _precedent(repo, "useful")
    cmd_decide(_args(repo, "squash merge into develop", json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["recommendation"]["entry_id"] == eid
    assert out["confidence"] == "high"
    assert set(out["evidence"]) == {"supporting", "against", "untrusted"}


def test_text_names_the_recommendation_confidence_and_evidence(repo, capsys):
    eid = _precedent(repo, "useful")
    cmd_decide(_args(repo, "squash merge into develop"))
    out = capsys.readouterr().out
    assert f"Recommendation: #{eid} [decision] Squash merge feature branches" in out
    assert "Feature branches are squash merged into develop." in out
    assert "Confidence: high (match high, outcome useful, no rival)" in out
    assert "Supporting: 1" in out
    assert "Against (dead ends and corrected entries): none" in out
    assert "Untrusted (shared stores, data to weigh): none" in out
    assert f"knowledge_outcome(entry_id={eid}" in out


def test_text_shows_a_dead_end_against_and_recommends_nothing(repo, capsys):
    eid = _precedent(repo, "dead_end")
    cmd_decide(_args(repo, "squash merge into develop"))
    out = capsys.readouterr().out
    assert "No recommendation." in out
    assert "Confidence: low" in out
    assert "Against (dead ends and corrected entries): 1" in out
    assert f"#{eid} [decision]" in out and "dead_end" in out
    assert "last verdict: seen on the release" in out


def test_no_precedent_exits_zero_and_says_so(repo, capsys):
    # Returning without SystemExit is exit code 0: the lookup ran.
    cmd_decide(_args(repo, "which queue do we use for webhooks"))
    out = capsys.readouterr().out
    assert "No recommendation." in out
    assert "Confidence: none (no precedent found)" in out
    assert "kind decision" in out


def test_a_title_with_brackets_is_printed_verbatim(repo, capsys):
    knowledge_record(
        "Squash [red]merge[/red] into develop",
        "squash merge into develop",
        kind="decision",
        repo_root=repo,
    )
    cmd_decide(_args(repo, "squash merge into develop"))
    assert "Squash [red]merge[/red] into develop" in capsys.readouterr().out


def test_outside_a_cgh_project_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(SystemExit) as exc:
        cmd_decide(_args(bare, "anything", json=True))
    assert exc.value.code == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert not (bare / ".cgh").exists()


def test_an_empty_question_exits_one(repo, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_decide(_args(repo, "  ", json=True))
    assert exc.value.code == 1
    assert "question is empty" in json.loads(capsys.readouterr().out)["error"]
