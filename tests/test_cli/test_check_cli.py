# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for `cgh check`: the five exit codes (0 allowed and only
#              allowed, 2 refused, 3 ask, 4 no rule, 1 error), the text output
#              and --json, which prints the check_action payload.

from __future__ import annotations

import argparse
import json
import sqlite3

import pytest

import cgh.state.call_log as cl
from cgh.cli.commands_decision import cmd_check, register_decision_parser
from cgh.state.call_log import knowledge_record

_BODY = (
    "Never push to an integration branch.\n\n"
    "cgh-rule: refuse git.push branch=main|develop\n"
    "cgh-rule: ask git.tag tag=v*\n"
    "cgh-rule: allow git.push branch=feature/*\n"
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    monkeypatch.setattr(
        "cgh.state.action_rules.current_branch", lambda _p, timeout=0: "feature/x"
    )
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    cl.reset_for_tests()
    yield root
    cl.reset_for_tests()


def _run(root, action: str, as_json: bool = False, session_id: str = "") -> int:
    args = argparse.Namespace(
        action=action, root=str(root), json=as_json, session_id=session_id
    )
    with pytest.raises(SystemExit) as exc:
        cmd_check(args)
    return exc.value.code


def _rules(root, body: str = _BODY, **kwargs) -> int:
    return knowledge_record(
        "No direct push", body, kind="standing_instruction", repo_root=root, **kwargs
    )


@pytest.mark.parametrize(
    ("action", "code"),
    [
        ("git push origin feature/x", 0),
        ("git push origin develop", 2),
        ("git tag v0.22.0", 3),
        ("git commit -m x", 4),
        ("faire le necessaire", 4),
    ],
)
def test_exit_code_per_verdict(repo, action, code):
    _rules(repo)
    assert _run(repo, action) == code


def test_exit_zero_is_reserved_for_a_matching_allow(repo):
    # A silent store: nothing is allowed, whatever the action.
    for action in ("git push origin feature/x", "ls", "git status"):
        assert _run(repo, action) == 4, action
    _rules(repo, "cgh-rule: refuse git.push branch=develop\n")
    assert _run(repo, "git push origin feature/x") == 4
    # A compound command is allowed only when every segment is.
    _rules(repo, "cgh-rule: allow git.push branch=feature/*\n")
    assert _run(repo, "git push origin feature/x") == 0
    assert _run(repo, "git push origin feature/x && git status") == 4


def test_no_store_is_an_error(tmp_path, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _run(bare, "git push origin develop") == 1
    assert "no cgh store" in capsys.readouterr().err
    assert not (bare / ".cgh").exists()


def test_an_unreadable_store_is_an_error(repo, monkeypatch, capsys):
    def broken(*_a, **_k):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(cl, "standing_rule_entries", broken)
    assert _run(repo, "git push origin develop") == 1
    err = capsys.readouterr().err
    assert "cannot read the knowledge store" in err


def test_json_prints_the_tool_payload(repo, capsys):
    entry_id = _rules(repo)
    assert _run(repo, "git push origin develop", as_json=True) == 2
    out = json.loads(capsys.readouterr().out)
    assert out == cl.check_action("git push origin develop", "", repo)
    assert out["verdict"] == "refused"
    assert out["rule"]["entry_id"] == entry_id
    assert set(out) == {
        "action",
        "verdict",
        "recognised",
        "rule",
        "actions",
        "others",
        "ignored",
        "next",
    }


def test_json_carries_recognised_with_no_rule(repo, capsys):
    assert _run(repo, "faire le necessaire", as_json=True) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "no_rule" and out["recognised"] is False
    assert _run(repo, "git commit -m x", as_json=True) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "no_rule" and out["recognised"] is True


def test_text_output_names_the_verdict_the_rule_and_the_reason(repo, capsys):
    entry_id = _rules(repo)
    assert _run(repo, "git push origin develop") == 2
    text = capsys.readouterr().out
    lines = text.splitlines()
    assert lines[0].startswith("refused")
    assert "cgh-rule: refuse git.push branch=main|develop" in lines[1]
    assert f"#{entry_id}" in lines[1]
    assert "Never push to an integration branch." in text
    assert "Do not proceed" in lines[-1]


def test_session_id_ignores_an_allow_the_session_recorded(repo, capsys):
    _rules(repo, "cgh-rule: allow git.push\n", session_id="sess-a")
    assert _run(repo, "git push origin x", session_id="sess-b") == 0
    capsys.readouterr()
    assert _run(repo, "git push origin x", session_id="sess-a") == 4
    assert "ignored (recorded by this session)" in capsys.readouterr().out


def test_parser_contract():
    parser = argparse.ArgumentParser()
    register_decision_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["check", "git push", "--json", "--session-id", "s1"])
    assert args.action == "git push"
    assert args.json is True and args.session_id == "s1"
    assert parser.parse_args(["check", "x"]).session_id == ""


def test_check_is_wired_into_the_cgh_command(repo, monkeypatch, capsys):
    import cgh.__main__ as cgh_main

    _rules(repo)
    monkeypatch.setattr(
        "sys.argv", ["cgh", "check", "merge PR 12", "--root", str(repo), "--json"]
    )
    with pytest.raises(SystemExit) as exc:
        cgh_main.main()
    assert exc.value.code == 4
    out = json.loads(capsys.readouterr().out)
    assert out["actions"][0]["verb"] == "gh.pr.merge"
