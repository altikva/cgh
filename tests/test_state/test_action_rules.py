# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for cgh/state/action_rules.py: parsing rule lines, each
#              recogniser, refspecs, compound commands, phrase aliases, the
#              verdict precedence, the allow rule a session recorded itself,
#              the branch read that gives up instead of failing, and the
#              directory each action runs in (`cd`, `git -C`), with the cases
#              where it is certain enough to change which branch is read.

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cgh.state import action_rules as ar

_ON_PATH = {"rm", "ls", "curl", "make"}


def _which(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in _ON_PATH else None


def _norm(action: str, branch: str | None = "feature/x") -> list[tuple[str, dict]]:
    return [
        (a.verb, a.fields)
        for a in ar.normalise(
            action, cwd="/repo", branch_reader=lambda _p: branch, which=_which
        )
    ]


def _entry(
    entry_id: int, body: str, title: str = "rule", session_id: str = "", ts: float = 0.0
) -> dict:
    return {
        "id": entry_id,
        "title": title,
        "body": body,
        "session_id": session_id,
        "ts": ts,
    }


def _check(
    action: str,
    entries: list[dict],
    branch: str | None = "feature/x",
    session_id: str = "",
) -> dict:
    return ar.evaluate(
        action,
        entries,
        session_id=session_id,
        cwd="/repo",
        branch_reader=lambda _p: branch,
        which=_which,
    )


# ---------------------------------------------------------------------------
# Rule lines
# ---------------------------------------------------------------------------


def test_rule_lines_are_parsed_out_of_the_prose():
    body = (
        "Never push to an integration branch.\n\n"
        "cgh-rule: refuse git.push branch=main|master|develop\n"
        "  cgh-rule: ask git.tag tag=v*\n"
        "cgh-rule: ask *.publish\n"
    )
    rules, errors = ar.parse_rule_lines(body)
    assert errors == []
    assert [(r.verdict, r.verb) for r in rules] == [
        ("refuse", "git.push"),
        ("ask", "git.tag"),
        ("ask", "*.publish"),
    ]
    assert dict(rules[0].constraints) == {"branch": ("main", "master", "develop")}
    assert rules[1].line == "cgh-rule: ask git.tag tag=v*"
    assert ar.reason_of(body) == "Never push to an integration branch."


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("cgh-rule: forbid git.push", "unknown verdict"),
        ("cgh-rule: refuse", "expected"),
        ("cgh-rule refuse git.push", "cgh-rule:"),
        ("cgh-rule: refuse git.push branch", "key=glob"),
        ("cgh-rule: refuse git.push branch=", "empty glob"),
        ("cgh-rule: refuse git.push branch=a branch=b", "duplicate"),
        ("cgh-rule: refuse git/push", "invalid verb"),
    ],
)
def test_a_line_that_does_not_parse_is_reported_and_never_rules(line, fragment):
    rules, errors = ar.parse_rule_lines(f"reason\n{line}\n")
    assert rules == []
    assert len(errors) == 1
    assert errors[0]["line"] == line
    assert fragment in errors[0]["error"]
    # The broken line rules on nothing, whatever it meant to say.
    out = _check("git push origin main", [_entry(1, f"reason\n{line}\n")])
    assert out["verdict"] == "no_rule"
    assert out["rule"] is None


def test_prose_mentioning_rules_is_not_a_rule_line():
    rules, errors = ar.parse_rule_lines("Write a cgh-rule: line to rule.\nNo rule.")
    assert rules == [] and errors == []


# ---------------------------------------------------------------------------
# Recognisers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("git tag v0.22.0", [("git.tag", {"tag": "v0.22.0"})]),
        ("git tag -a v1 -m 'release one'", [("git.tag", {"tag": "v1"})]),
        ("git commit -m msg", [("git.commit", {"on_branch": "feature/x"})]),
        (
            "git commit --amend --no-edit",
            [("git.commit", {"on_branch": "feature/x", "amend": "yes"})],
        ),
        ("git branch -D old", [("git.branch.delete", {"branch": "old"})]),
        ("git reset --hard HEAD~1", [("git.reset", {"hard": "yes"})]),
        ("git rebase main", [("git.rebase", {"branch": "main"})]),
        ("git merge develop", [("git.merge", {"branch": "develop"})]),
        ("git checkout -b feature/y", [("git.checkout", {"branch": "feature/y"})]),
        ("git stash", [("git.stash", {})]),
        (
            "gh pr merge 12 --squash --admin",
            [("gh.pr.merge", {"pr": "12", "method": "squash", "admin": "yes"})],
        ),
        ("gh pr create --base develop -t x", [("gh.pr.create", {"base": "develop"})]),
        ("gh pr close 7", [("gh.pr.close", {"pr": "7"})]),
        (
            "gh release create v0.22.0 --notes x",
            [("gh.release.create", {"tag": "v0.22.0"})],
        ),
        (
            "gh issue create -R altikva/cgh -t x",
            [("gh.issue.create", {"repo": "altikva/cgh"})],
        ),
        ("uv publish", [("pkg.publish", {"tool": "uv"})]),
        ("twine upload dist/*", [("pkg.publish", {"tool": "twine"})]),
        ("npm publish", [("pkg.publish", {"tool": "npm"})]),
        ("pnpm publish", [("pkg.publish", {"tool": "pnpm"})]),
        ("cargo publish", [("pkg.publish", {"tool": "cargo"})]),
        (
            "uv tool install cgh",
            [("tool.install", {"tool": "uv", "package": "cgh"})],
        ),
        (
            "pipx upgrade cgh",
            [("tool.install", {"tool": "pipx", "package": "cgh"})],
        ),
        ("pkill -f cgh", [("proc.kill", {"pattern": "cgh"})]),
        ("killall python", [("proc.kill", {"pattern": "python"})]),
    ],
)
def test_recognisers(action, expected):
    assert _norm(action) == expected


@pytest.mark.parametrize(
    "action", ["git tag", "git tag -l 'v*'", "git tag -d v1", "git reset HEAD f"]
)
def test_a_git_form_the_table_leaves_out_falls_back_to_shell(action):
    got = _norm(action)
    assert [verb for verb, _f in got] == ["shell.git"]
    assert got[0][1]["args"] == action[len("git ") :].replace("'", "")


def test_an_unknown_command_becomes_shell_argv0_with_its_args():
    assert _norm("rm -rf build") == [("shell.rm", {"args": "-rf build"})]
    assert _norm("uv run pytest -q") == [("shell.uv", {"args": "run pytest -q"})]


# ---------------------------------------------------------------------------
# git push refspecs
# ---------------------------------------------------------------------------


def test_push_names_the_destination_of_each_refspec():
    got = _norm("git push origin HEAD:main feature/x:refs/heads/develop")
    assert got == [
        ("git.push", {"remote": "origin", "branch": "main"}),
        ("git.push", {"remote": "origin", "branch": "develop"}),
    ]


def test_bare_push_takes_the_current_branch():
    assert _norm("git push", branch="develop") == [("git.push", {"branch": "develop"})]
    assert _norm("git push origin @", branch="develop") == [
        ("git.push", {"remote": "origin", "branch": "develop"})
    ]
    assert _norm("git push origin HEAD", branch="develop") == [
        ("git.push", {"remote": "origin", "branch": "develop"})
    ]


def test_push_force_tags_and_delete():
    assert _norm("git push -f origin main")[0][1]["force"] == "yes"
    assert _norm("git push --force-with-lease origin main")[0][1]["force"] == "yes"
    assert _norm("git push origin +main")[0][1] == {
        "remote": "origin",
        "branch": "main",
        "force": "yes",
    }
    assert _norm("git push --tags")[0][1]["tags"] == "yes"
    assert _norm("git push origin --delete old") == [
        ("git.push", {"remote": "origin", "branch": "old", "delete": "yes"}),
        ("git.branch.delete", {"branch": "old"}),
    ]
    assert _norm("git push origin :old")[1] == ("git.branch.delete", {"branch": "old"})


def test_a_field_git_cannot_read_is_absent_and_its_rule_does_not_match():
    assert _norm("git push", branch=None) == [("git.push", {})]
    assert _norm("git commit -m x", branch=None) == [("git.commit", {})]
    rule = _entry(1, "cgh-rule: refuse git.commit on_branch=develop")
    out = _check("git commit -m x", [rule], branch=None)
    assert out["verdict"] == "no_rule"
    assert out["actions"][0]["fields"] == {}


# ---------------------------------------------------------------------------
# Compound commands and wrappers
# ---------------------------------------------------------------------------


def test_compound_commands_split_on_the_shell_separators():
    got = _norm("git commit -m 'a && b; c' && git push origin main | rm x\nls -la")
    assert [verb for verb, _f in got] == [
        "git.commit",
        "git.push",
        "shell.rm",
        "shell.ls",
    ]


def test_wrappers_assignments_and_redirections_are_dropped():
    assert _norm("FOO=1 sudo -u bob env BAR=2 pkill -f cgh") == [
        ("proc.kill", {"pattern": "cgh"})
    ]
    assert _norm("time git push origin main > out.log 2>&1")[0] == (
        "git.push",
        {"remote": "origin", "branch": "main"},
    )


def test_a_shell_script_argument_is_read_through():
    assert _norm("bash -c 'git push -f origin main'") == [
        ("git.push", {"remote": "origin", "branch": "main", "force": "yes"})
    ]
    assert _norm('sh -c "cd x && git tag v1"')[-1] == ("git.tag", {"tag": "v1"})


_PUSH = ("git.push", {"remote": "origin", "branch": "main", "force": "yes"})


@pytest.mark.parametrize(
    "action",
    [
        "echo $(git push -f origin main)",
        'echo "$(git push -f origin main)"',
        "echo `git push -f origin main`",
        "diff <(git push -f origin main) x",
        "(git push -f origin main)",
        "bash -lc 'git push -f origin main'",
        "eval git push -f origin main",
        "eval 'git push -f origin main'",
        'env -S "git push -f origin main"',
        "xargs -n 1 git push -f origin main",
        "sudo -i git push -f origin main",
        "ls | xargs git push -f origin main",
        "sudo -n git push -f origin main",
        "sudo --user bob git push -f origin main",
        "sudo -r role git push -f origin main",
        "timeout --signal KILL 30 git push -f origin main",
        "nice --adjustment 5 git push -f origin main",
        "xargs -J % git push -f origin main",
        "sudo /usr/bin/git push -f origin main",
        "timeout -k 2 30 git push -f origin main",
        "nice -n 10 git push -f origin main",
        "doas -u bob git push -f origin main",
        "stdbuf -oL git push -f origin main",
        "git push -f \\\n  origin main",
        "sudo -E -n git push -f origin main",
        "env -u HOME git push -f origin main",
        "time -o t.log git push -f origin main",
        'ls > "$(git push -f origin main)"',
        "ls > `git push -f origin main`",
        "ls > >(git push -f origin main)",
        "ls 2> >(git push -f origin main)",
        "ls < <(git push -f origin main)",
    ],
)
def test_a_command_inside_another_is_still_read(action):
    assert _PUSH in _norm(action), _norm(action)
    refuse = _entry(1, "cgh-rule: refuse git.push force=yes")
    allow_rest = _entry(2, "cgh-rule: allow shell.*")
    assert _check(action, [refuse, allow_rest])["verdict"] == "refused"


@pytest.mark.parametrize(
    "action",
    ["sudo -u bob", "env -S ''", 'echo "$(  )"', "FOO=1", "bash -c ''"],
)
def test_a_segment_is_never_dropped_into_an_allowed_command(action):
    allow_ls = _entry(1, "cgh-rule: allow shell.ls")
    alone = _check(action, [allow_ls])
    assert alone["verdict"] == "no_rule"
    both = _check(f"ls && {action}", [allow_ls])
    assert both["verdict"] == "no_rule", both["actions"]
    assert _check("ls", [allow_ls])["verdict"] == "allowed"


def test_a_flag_that_takes_a_value_for_one_wrapper_is_plain_for_another():
    # -n takes a value for xargs and none for sudo; -S is a script for env only.
    assert _norm("xargs -n 1 rm") == [("shell.rm", {"args": ""})]
    assert _norm("sudo -n rm x") == [("shell.rm", {"args": "x"})]
    assert _norm("sudo -S rm x") == [("shell.rm", {"args": "x"})]


def test_a_newline_after_an_escaped_backslash_separates_two_commands():
    got = _norm("ls x\\\\\ngit push -f origin main")
    assert [verb for verb, _f in got] == ["shell.ls", "git.push"]
    # Inside single quotes a backslash is literal: the newline is not a
    # continuation and the quoted text stays one argument.
    assert _norm("ls 'a\\\nb'") == [("shell.ls", {"args": "a\\\nb"})]


def test_push_of_a_tag_by_name_is_a_tag():
    assert _norm("git push origin tag v1.0") == [
        ("git.push", {"remote": "origin", "tag": "v1.0"})
    ]
    rule = _entry(1, "cgh-rule: ask git.push tag=v*")
    assert _check("git push origin tag v1.0", [rule])["verdict"] == "ask"


_BROAD_ALLOW = _entry(
    40,
    "cgh-rule: refuse git.push branch=main|develop\n"
    "cgh-rule: refuse git.commit on_branch=main\n"
    "cgh-rule: allow git.push\n"
    "cgh-rule: allow git.commit\n",
)


@pytest.mark.parametrize(
    ("action", "branch"),
    [
        ("git push --all origin", "main"),
        ("git push --mirror origin", "main"),
        ("git push origin :", "main"),
        ("git push", None),
        ("git push origin HEAD", None),
        ("git -C /somewhere/else push", "main"),
        ("git --git-dir=.git push", "main"),
        ("git commit -m x", None),
        ("git -C /somewhere/else commit -m x", "main"),
    ],
)
def test_an_allow_does_not_clear_a_push_whose_branch_is_unknown(action, branch):
    out = _check(action, [_BROAD_ALLOW], branch=branch)
    assert out["verdict"] == "no_rule", out["actions"]
    assert out["rule"] is None
    assert out["actions"][0]["rule_entry_id"] is None
    # The allow rule matched and is shown, it just did not decide.
    assert {"entry_id": 40, "verdict": "allowed"} in [
        {"entry_id": o["entry_id"], "verdict": o["verdict"]} for o in out["others"]
    ]


def test_a_known_branch_is_still_allowed_by_the_broad_allow():
    for action in ("git push", "git push origin feature/x", "git commit -m x"):
        assert _check(action, [_BROAD_ALLOW], branch="feature/x")["verdict"] == (
            "allowed"
        ), action
    assert _check("git push", [_BROAD_ALLOW], branch="main")["verdict"] == "refused"


def test_a_field_that_does_not_apply_never_withholds_an_allow():
    # A plain push has no `force` field: that is not an unknown, so the
    # refusal of forced pushes does not hold back the allow.
    rules = _entry(1, "cgh-rule: refuse git.push force=yes\ncgh-rule: allow git.push")
    assert _check("git push origin feature/x", [rules])["verdict"] == "allowed"
    assert _check("git push -f origin feature/x", [rules])["verdict"] == "refused"
    # An unknown branch with no rule constraining it: the allow decides.
    assert _check("git push", [rules], branch=None)["verdict"] == "allowed"


def test_an_unreadable_dash_c_argument_is_an_absent_field(tmp_path):
    got = ar.normalise("git -C 'a\x00b' commit -m x", cwd=tmp_path, which=_which)
    assert got[0].verb == "git.commit" and got[0].fields == {}


def test_a_redirection_target_is_not_a_command():
    assert _norm("ls > git 2>&1") == [("shell.ls", {"args": "2"})]
    assert _norm("ls >> out.log < in.txt") == [("shell.ls", {"args": ""})]


def test_git_dash_c_reads_the_branch_of_that_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    seen: list[Path] = []

    def reader(path: Path) -> str:
        seen.append(path)
        return "develop" if path.name == "sub" else "feature/x"

    got = ar.normalise("git -C sub commit -m x", cwd=tmp_path, branch_reader=reader)
    assert got[0].fields == {"on_branch": "develop"}
    assert seen == [(tmp_path / "sub").resolve()]


@pytest.mark.parametrize("where", ["..", "../other", "/", "sub/../.."])
def test_git_is_never_run_in_a_directory_outside_the_checkout(tmp_path, where):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    seen: list[Path] = []

    def reader(path: Path) -> str:
        seen.append(path)
        return "develop"

    got = ar.normalise(f"git -C {where} commit -m x", cwd=repo, branch_reader=reader)
    assert seen == []
    assert got[0].verb == "git.commit"
    assert got[0].fields == {}


def test_the_branch_is_read_once_per_check():
    calls: list[Path] = []

    def reader(path: Path) -> str:
        calls.append(path)
        return "develop"

    ar.normalise(
        "git commit -m a && git push && git commit -m b",
        cwd="/repo",
        branch_reader=reader,
    )
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("merge PR 12", ("gh.pr.merge", {"pr": "12"})),
        ("fusionner la PR 12", ("gh.pr.merge", {"pr": "12"})),
        ("Merge the pull request #12", ("gh.pr.merge", {"pr": "12"})),
        ("tag v0.22.0", ("git.tag", {"tag": "v0.22.0"})),
        ("push to develop", ("git.push", {"branch": "develop"})),
        ("pousser sur develop", ("git.push", {"branch": "develop"})),
        ("release v0.22.0", ("gh.release.create", {"tag": "v0.22.0"})),
        ("commit on main", ("git.commit", {"on_branch": "main"})),
    ],
)
def test_phrase_aliases_french_and_english(phrase, expected):
    assert _norm(phrase) == [expected]


def test_a_phrase_never_hides_the_command_that_follows_it():
    got = _norm("merge PR 12 && git push -f origin main")
    assert ("git.push", {"remote": "origin", "branch": "main", "force": "yes"}) in got
    rule = _entry(1, "cgh-rule: refuse git.push force=yes")
    assert _check("merge PR 12 && git push -f origin main", [rule])["verdict"] == (
        "refused"
    )
    assert _check("tag v1; git push -f origin main", [rule])["verdict"] == "refused"


@pytest.mark.parametrize(
    "text", ["", "   ", "deployer en prod", "ship it please", "git push 'unterminated"]
)
def test_what_cannot_be_read_normalises_to_nothing(text):
    assert _norm(text) == []
    out = _check(text, [_entry(1, "cgh-rule: allow *")])
    assert out["recognised"] is False
    assert out["verdict"] == "no_rule"
    assert out["actions"] == []
    assert "could not read" in out["next"]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

_INTEGRATION = _entry(
    10,
    "Never push to an integration branch: every change goes through a pull "
    "request. Releases are discussed before the tag.\n\n"
    "cgh-rule: refuse git.push branch=main|master|develop\n"
    "cgh-rule: refuse git.commit on_branch=main|master|develop\n"
    "cgh-rule: ask git.tag tag=v*\n"
    "cgh-rule: ask gh.release.create\n"
    "cgh-rule: ask *.publish\n",
    title="No direct push, no tag without a go",
    ts=1795290000.0,
)


def test_no_tag_without_an_explicit_go_says_ask():
    out = _check("git tag v0.22.0", [_INTEGRATION])
    assert out["verdict"] == "ask"
    assert out["rule"] == {
        "entry_id": 10,
        "title": "No direct push, no tag without a go",
        "line": "cgh-rule: ask git.tag tag=v*",
        "reason": (
            "Never push to an integration branch: every change goes through a "
            "pull request. Releases are discussed before the tag."
        ),
        "recorded": "2026-11-21T19:40Z",
    }
    assert "Ask the user" in out["next"] and "#10" in out["next"]


def test_a_refuse_rule_on_the_tag_says_refused():
    out = _check("git tag v0.22.0", [_entry(3, "cgh-rule: refuse git.tag")])
    assert out["verdict"] == "refused"
    assert out["rule"]["entry_id"] == 3
    assert out["next"].startswith("Do not proceed")


def test_push_to_develop_is_refused_from_develop_and_by_name():
    for action in ("git push origin develop", "git push"):
        out = _check(action, [_INTEGRATION], branch="develop")
        assert out["verdict"] == "refused", action
        assert out["rule"]["line"] == (
            "cgh-rule: refuse git.push branch=main|master|develop"
        )


def test_push_of_a_feature_branch_is_allowed_only_by_an_allow_rule():
    silent = _check("git push origin feature/x", [_INTEGRATION], branch="feature/x")
    assert silent["verdict"] == "no_rule"
    allow = _entry(
        11, "Topic branches are free.\ncgh-rule: allow git.push branch=feature/*"
    )
    for action in ("git push origin feature/x", "git push"):
        out = _check(action, [_INTEGRATION, allow], branch="feature/x")
        assert out["verdict"] == "allowed", action
        assert out["rule"]["entry_id"] == 11
        assert out["next"].startswith("Proceed")


def test_no_rule_at_all_is_never_allowed():
    for entries in ([], [_entry(1, "Prose only, no rule line.")]):
        out = _check("git push origin develop", entries)
        assert out["verdict"] == "no_rule"
        assert out["recognised"] is True
        assert out["rule"] is None
        assert out["related"] == []
        assert "ask before anything hard to undo" in out["next"]


def test_an_allow_never_overrides_a_refuse_or_an_ask():
    allow_all = _entry(20, "cgh-rule: allow *", ts=2e9)
    out = _check("git push origin develop", [allow_all, _INTEGRATION])
    assert out["verdict"] == "refused"
    assert out["others"] == [
        {"entry_id": 20, "line": "cgh-rule: allow *", "verdict": "allowed"}
    ]
    assert _check("git tag v1", [allow_all, _INTEGRATION])["verdict"] == "ask"


def test_refuse_wins_over_ask():
    both = _entry(1, "cgh-rule: ask git.push\ncgh-rule: refuse git.push force=yes")
    assert _check("git push -f origin x", [both])["verdict"] == "refused"
    assert _check("git push origin x", [both])["verdict"] == "ask"


def test_the_strictest_segment_decides_a_compound_command():
    allow_push = _entry(2, "cgh-rule: allow git.push branch=feature/*")
    out = _check("git push origin feature/x && uv publish", [allow_push, _INTEGRATION])
    assert out["verdict"] == "ask"
    assert [a["verdict"] for a in out["actions"]] == ["allowed", "ask"]
    assert out["actions"][1]["raw"] == "uv publish"
    assert out["rule"]["line"] == "cgh-rule: ask *.publish"
    # One segment the store is silent on keeps the whole command from allowed.
    partial = _check("git push origin feature/x && rm -rf build", [allow_push])
    assert partial["verdict"] == "no_rule"
    assert [a["verdict"] for a in partial["actions"]] == ["allowed", "no_rule"]
    assert partial["actions"][0]["rule_entry_id"] == 2


def test_a_shell_rule_can_target_an_unrecognised_command():
    rule = _entry(5, "cgh-rule: refuse shell.rm args=*-rf*")
    assert _check("rm -rf build", [rule])["verdict"] == "refused"
    assert _check("rm build.log", [rule])["verdict"] == "no_rule"


def test_an_allow_recorded_by_the_calling_session_does_not_count():
    own = _entry(30, "cgh-rule: allow git.push", session_id="sess-a")
    mine = _check("git push origin feature/x", [own], session_id="sess-a")
    assert mine["verdict"] == "no_rule"
    assert mine["rule"] is None
    assert mine["ignored"] == [{"entry_id": 30, "line": "cgh-rule: allow git.push"}]
    # Another session, or the same rule read without a session id, is ruled.
    assert (
        _check("git push origin feature/x", [own], session_id="sess-b")["verdict"]
        == "allowed"
    )
    assert _check("git push origin feature/x", [own])["verdict"] == "allowed"


def test_a_refuse_recorded_by_the_calling_session_still_counts():
    own = _entry(31, "cgh-rule: refuse git.push", session_id="sess-a")
    out = _check("git push origin feature/x", [own], session_id="sess-a")
    assert out["verdict"] == "refused"
    assert out["ignored"] == []


# ---------------------------------------------------------------------------
# Answer shape
# ---------------------------------------------------------------------------

_KEYS = {
    "action",
    "verdict",
    "recognised",
    "rule",
    "actions",
    "others",
    "ignored",
    "next",
}


def test_answer_key_sets():
    matched = _check("git push origin develop", [_INTEGRATION])
    assert set(matched) == _KEYS
    assert set(matched["rule"]) == {"entry_id", "title", "line", "reason", "recorded"}
    assert set(matched["actions"][0]) == {
        "verb",
        "fields",
        "raw",
        "verdict",
        "rule_entry_id",
    }
    assert matched["action"] == "git push origin develop"
    # `related` exists only with no_rule.
    assert set(_check("git push origin x", [_INTEGRATION])) == _KEYS | {"related"}
    assert set(_check("git push origin x", [])) == _KEYS | {"related"}


# ---------------------------------------------------------------------------
# Reading the branch never fails the check
# ---------------------------------------------------------------------------


def test_the_branch_read_has_a_two_second_budget():
    assert ar.GIT_BRANCH_TIMEOUT_S == 2.0


def test_current_branch_reads_a_real_repository(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=30
        )

    git("init", "-q", "-b", "develop")
    assert ar.current_branch(tmp_path) is None  # no commit yet: HEAD is unborn
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "init",
    )
    assert ar.current_branch(tmp_path) == "develop"


def test_current_branch_outside_a_repository_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert ar.current_branch(tmp_path) is None
    assert ar.current_branch(tmp_path / "missing") is None


def test_a_slow_git_gives_an_absent_field_not_an_error(tmp_path, monkeypatch):
    seen: dict = {}

    def slow(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(ar.subprocess, "run", slow)
    assert ar.current_branch(tmp_path) is None
    assert seen["timeout"] == ar.GIT_BRANCH_TIMEOUT_S
    out = ar.evaluate(
        "git commit -m x",
        [_entry(1, "cgh-rule: refuse git.commit on_branch=develop")],
        cwd=tmp_path,
        which=_which,
    )
    assert out["verdict"] == "no_rule"
    assert out["actions"] == [
        {
            "verb": "git.commit",
            "fields": {},
            "raw": "git commit -m x",
            "verdict": "no_rule",
            "rule_entry_id": None,
        }
    ]


@pytest.mark.parametrize(("given", "used"), [(None, 2.0), (0.1, 0.1), (0.05, 0.05)])
def test_the_caller_sets_the_git_budget(tmp_path, monkeypatch, given, used):
    seen: list[float] = []

    def fake(argv, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(ar.subprocess, "run", fake)
    out = ar.evaluate("git commit -m x", [], cwd=tmp_path, git_timeout=given)
    assert seen == [used]
    assert out["actions"][0]["fields"] == {}
    seen.clear()
    ar.normalise("git push", cwd=tmp_path, git_timeout=given)
    assert seen == [used]


def test_check_action_passes_the_git_budget_through(tmp_path, monkeypatch):
    import cgh.state.call_log as cl

    (tmp_path / ".cgh").mkdir()
    cl.reset_for_tests()
    seen: list[float] = []

    def fake(argv, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(ar.subprocess, "run", fake)
    try:
        out = cl.check_action("git commit -m x", "", tmp_path, git_timeout=0.1)
        assert seen == [0.1]
        assert out["verdict"] == "no_rule" and out["actions"][0]["fields"] == {}
        seen.clear()
        cl.check_action("git commit -m x", "", tmp_path)
        assert seen == [2.0]
    finally:
        cl.reset_for_tests()


def test_the_timeout_really_cuts_a_hanging_process(tmp_path, monkeypatch):
    real_run = subprocess.run

    def hang(_argv, **kwargs):
        return real_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(ar.subprocess, "run", hang)
    start = time.monotonic()
    assert ar.current_branch(tmp_path, timeout=0.3) is None
    assert time.monotonic() - start < 10


def test_a_missing_git_binary_is_an_absent_field(tmp_path, monkeypatch):
    def missing(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(ar.subprocess, "run", missing)
    assert ar.current_branch(tmp_path) is None


# ---------------------------------------------------------------------------
# No model, no network
# ---------------------------------------------------------------------------

_FORBIDDEN = {
    "anthropic",
    "openai",
    "httpx",
    "requests",
    "urllib",
    "http",
    "socket",
    "litellm",
    "fastmcp",
    "aiohttp",
}


@pytest.mark.parametrize(
    "module",
    [
        "cgh/state/action_rules.py",
        "cgh/server/tools_decision.py",
        "cgh/cli/commands_decision.py",
    ],
)
def test_the_check_imports_no_model_and_no_network_client(module):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & _FORBIDDEN, imported & _FORBIDDEN


def test_a_check_opens_no_socket(monkeypatch):
    import socket

    def refuse(*_a, **_k):
        raise AssertionError("check_action must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    out = _check("git push origin develop && uv publish", [_INTEGRATION])
    assert out["verdict"] == "refused"


# ---------------------------------------------------------------------------
# Where an action runs
# ---------------------------------------------------------------------------

_COMMIT_ON_DEVELOP = "cgh-rule: refuse git.commit on_branch=develop\n"


@pytest.fixture
def two(tmp_path):
    """Two directories: the caller's, on develop, and another, on feature/x."""
    here = (tmp_path / "here").resolve()
    there = (tmp_path / "there").resolve()
    (here / "sub").mkdir(parents=True)
    there.mkdir()
    return here, there


def _reader(here: Path, there: Path):
    def read(path: Path) -> str | None:
        if path == there or there in path.parents:
            return "feature/x"
        if path == here or here in path.parents:
            return "develop"
        return None

    return read


def _where(action: str, cwd: Path) -> list[str | None]:
    acts = ar.normalise(action, cwd=cwd, branch_reader=lambda _p: None, which=_which)
    return [a.where for a in acts if not a.verb.startswith("shell.")]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push", "here"),
        ("git -C {there} push", "there"),
        ("git -C sub push", "here/sub"),
        ("cd {there} && git push", "there"),
        ("cd sub && git push", "here/sub"),
        ("cd sub && cd .. && git push", "here"),
        ("pushd {there} && git push", "there"),
        ("cd {there} && git -C {here} push", "here"),
        ("bash -c 'cd {there} && git push'", "there"),
        ("sudo -u me git -C {there} push", "there"),
        # A guess, kept for the rules of the repo it points at.
        ("cd {there}; git push", "there"),
        ("true || cd {there} && git push", "there"),
        # Not the shell's own cd: the line did not move.
        ("env cd {there} && git push", "here"),
        ("/usr/bin/cd {there} && git push", "here"),
        ("( cd {there} ) ; git push", "there"),
        # Nowhere cgh can name.
        ('cd "$DIR" && git push', ""),
        ("cd - && git push", ""),
        ("cd {there} && popd && git push", ""),
        ("git -C $DIR push", ""),
        ("command cd {there} && git push", "there"),
        ("builtin cd {there} && git push", "there"),
        ("time -p cd {there} && git push", "there"),
        # GIT_DIR on the line changes what is certain, not where it points.
        ("GIT_DIR={here}/.git git -C {there} push", "there"),
        ("export GIT_DIR={here}/.git; cd {there} && git push", "there"),
        ("command cd - && git push", ""),
        ("git --git-dir={there}/.git push", ""),
    ],
)
def test_each_action_carries_the_directory_it_runs_in(two, command, expected):
    here, there = two
    names = {"here": str(here), "there": str(there), "here/sub": str(here / "sub")}
    got = _where(command.format(here=here, there=there), here)
    assert got == [names.get(expected, expected)]


def test_a_cd_inside_a_script_does_not_move_the_line_outside(two):
    here, there = two
    command = f"bash -c 'cd {there} && git status' && git push"
    assert _where(command, here) == [str(here)]


def test_an_action_not_read_from_a_command_has_no_directory():
    assert ar.Action("git.push").where is None
    assert [a.where for a in ar.normalise("push to develop", cwd="/repo")] == [None]


def _commit_verdict(command: str, here: Path, there: Path, **kwargs) -> str:
    out = ar.evaluate(
        command.format(here=here, there=there),
        [_entry(1, _COMMIT_ON_DEVELOP)],
        cwd=here,
        branch_reader=_reader(here, there),
        which=_which,
        **kwargs,
    )
    return out["verdict"]


def test_a_certain_cd_elsewhere_drops_the_branch_of_the_caller(two):
    # The commit is made in `there`: the branch of `here` says nothing of it.
    for command in (
        "cd {there} && git commit -m x",
        "cd {there} && make && git commit -m x",
        "(cd {there} && git commit -m x)",
        "git -C {there} commit -m x",
        "make ; cd {there} && git commit -m x",
        "cd /nowhere ; cd {there} && git commit -m x",
        "X=1 cd {there} && git commit -m x",
    ):
        assert _commit_verdict(command, *two) == "no_rule", command
    assert _commit_verdict("cd sub && git commit -m x", *two) == "refused"


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "( cd {there} ) ; git commit -m x",
        "cd {there} ; git commit -m x",
        "cd {there} || git commit -m x",
        "cd {there} | git commit -m x",
        "cd {there} && cd - && git commit -m x",
        "pushd {there} && popd && git commit -m x",
        'cd "$DIR" && git commit -m x',
        "cd {there}/$SUB && git commit -m x",
        "cd {there} && true ; git commit -m x",
        "bash -c 'cd {there}' && git commit -m x",
        # A cd that may never run.
        "true || cd {there} && git commit -m x",
        "test -d /nowhere || cd {there} && git commit -m x",
        "cd sub || cd {there} && git commit -m x",
        "echo x | cd {there} && git commit -m x",
        "true & cd {there} && git commit -m x",
        "( true ) && true || cd {there} && git commit -m x",
        # Behind a closing parenthesis cgh does not say what is certain.
        "( cd /nowhere ) ; cd {there} && git commit -m x",
        # A cd that another process runs: the shell stays where it was.
        "env cd {there} && git commit -m x",
        "/usr/bin/cd {there} && git commit -m x",
        "nohup cd {there} && git commit -m x",
        "sudo cd {there} && git commit -m x",
        "xargs cd {there} && git commit -m x",
        "command cd {there} && git commit -m x",
        "builtin cd {there} && git commit -m x",
        "time cd {there} && git commit -m x",
        "command -p cd {there} && git commit -m x",
        "pushd -n {there} && git commit -m x",
        "pushd +1 && git commit -m x",
        # git is pointed back at the caller's repository from elsewhere.
        "cd {there} && GIT_DIR={here}/.git git commit -m x",
        "cd {there} && env GIT_DIR={here}/.git GIT_WORK_TREE={here} git commit -m x",
        "export GIT_DIR={here}/.git && cd {there} && git commit -m x",
    ],
)
def test_in_doubt_the_branch_of_the_caller_still_rules(two, command):
    # The commit may well run where the caller stands: nothing is loosened.
    assert _commit_verdict(command, *two) == "refused"


def test_a_scope_judges_only_the_actions_that_run_in_it(two):
    here, there = two
    rule = [_entry(7, "cgh-rule: refuse git.commit on_branch=feature/*\n")]

    def verdict(command: str) -> str:
        out = ar.evaluate(
            command.format(there=there),
            rule,
            cwd=here,
            branch_reader=_reader(here, there),
            which=_which,
            scope=there,
        )
        return out["verdict"]

    assert verdict("git -C {there} commit -m x") == "refused"
    assert verdict("cd {there} && git commit -m x") == "refused"
    # A guess is enough to be judged where it points.
    assert verdict("cd {there} ; git commit -m x") == "refused"
    # The caller's own commit is not this repository's business.
    assert verdict("git commit -m x") == "no_rule"
    assert verdict("git -C {there} status && git commit -m x") == "no_rule"
    assert verdict('cd "$DIR" && git commit -m x') == "no_rule"
    # A word shaped like GIT_DIR=... does not take the action out of here.
    assert verdict("GIT_DIR=/x/.git git -C {there} commit -m x") == "refused"
    assert verdict("echo GIT_DIR=1 ; git -C {there} commit -m x") == "refused"
    assert verdict("command cd {there} && git commit -m x") == "refused"


def test_a_scope_that_cannot_be_resolved_judges_nothing(two, monkeypatch):
    _here, there = two
    monkeypatch.setattr(ar, "_resolved", lambda _p: None)
    out = ar.evaluate(
        "git commit -m x", [_entry(1, "cgh-rule: refuse git.commit\n")], scope=there
    )
    assert out["verdict"] == "no_rule"


def test_git_is_not_run_outside_the_caller_and_the_scope(two, tmp_path):
    here, there = two
    third = (tmp_path / "third").resolve()
    third.mkdir()
    asked: list[Path] = []

    def read(path: Path) -> str | None:
        asked.append(path)
        return "develop"

    for scope in (None, there):
        ar.normalise(
            f"git -C {third} commit -m x",
            cwd=here,
            branch_reader=read,
            which=_which,
            scope=scope,
        )
    assert asked == []
    ar.normalise(
        f"git -C {there} commit -m x", cwd=here, branch_reader=read, scope=there
    )
    assert asked == [there]


def test_segments_keep_the_operator_that_leads_to_them():
    linked = ar._linked_segments("a && b ; c || d | e\nf && ( g ) && h > out && i")
    assert [(op, argv[0]) for op, argv in linked] == [
        ("", "a"),
        ("&&", "b"),
        (";", "c"),
        ("||", "d"),
        ("|", "e"),
        (";", "f"),
        ("(", "g"),
        (")", "h"),
        ("&&", "i"),
    ]
