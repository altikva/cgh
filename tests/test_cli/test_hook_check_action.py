# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Bash precheck runs check_action first. Each verdict in each
#              mode (enforce, advise, off), how the mode is chosen, the stores
#              that say nothing, and the failures: a locked store, a slow git,
#              a dead owner, a corrupt store, a matcher that raises, a missed
#              deadline. A failure never blocks: exit 0 and an empty stdout.

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import stat
import subprocess
import time

import pytest

import cgh.state.action_rules as ar
import cgh.state.call_log as cl
from cgh.cli import commands_hooks as h
from cgh.cli import hook_check as hc

_PUSH = "git push origin develop"
_TAG = "git tag v1.2.3"
_BODY = (
    "Never push to an integration branch: every change goes through a pull "
    "request. Releases are discussed before the tag.\n\n"
    "cgh-rule: refuse git.push branch=main|master|develop\n"
    "cgh-rule: ask git.tag tag=v*\n"
    "cgh-rule: ask gh.release.create\n"
    "cgh-rule: refuse gh.pr.merge\n"
    "cgh-rule: allow git.push branch=feature/*\n"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    for env in (
        "CGH_HOOK_CHECK_ACTION",
        "CGH_HOOK_ANSWERS",
        "CGH_HOOK_BASH_ADVICE",
    ):
        monkeypatch.delenv(env, raising=False)
    # The branch of the checkout is the test's to choose, not the machine's.
    monkeypatch.setattr(ar, "current_branch", lambda _p, timeout=0: "feature/x")
    cl.reset_for_tests()
    yield
    cl.reset_for_tests()


@pytest.fixture
def repo(tmp_path):
    root = (tmp_path / "repo").resolve()
    (root / ".cgh").mkdir(parents=True)
    return root


def _rules(root, body: str = _BODY, **kwargs) -> int:
    """Record a standing instruction, then close the writer: the hook is
    another process and opens the store file on its own."""
    entry = cl.knowledge_record(
        "Integration branches and releases",
        body,
        kind="standing_instruction",
        repo_root=root,
        **kwargs,
    )
    cl.reset_for_tests()
    return entry


def _hook(monkeypatch, capsys, root, command: str, **payload) -> tuple[dict, str, str]:
    """Run the Bash precheck. Returns (hookSpecificOutput or {}, stdout,
    stderr), after asserting exit 0."""
    data = {"tool_input": {"command": command}, "cwd": str(root), **payload}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_bash(argparse.Namespace())
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)["hookSpecificOutput"] if captured.out else {}
    return out, captured.out, captured.err


def _enforce(monkeypatch) -> None:
    monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "enforce")


# --- enforce: the hook table ---------------------------------------------------


def test_refused_denies_and_cites_the_rule(monkeypatch, capsys, repo):
    entry = _rules(repo)
    _enforce(monkeypatch)
    out, _, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert f"#{entry}" in reason
    assert "cgh-rule: refuse git.push branch=main|master|develop" in reason
    assert "Never push to an integration branch" in reason
    assert "additionalContext" not in out
    assert err == ""


@pytest.mark.parametrize(
    ("command", "line"),
    [
        (_TAG, "cgh-rule: ask git.tag tag=v*"),
        (
            "gh release create v1.2.3 --generate-notes",
            "cgh-rule: ask gh.release.create",
        ),
    ],
)
def test_ask_hands_the_decision_to_the_user(monkeypatch, capsys, repo, command, line):
    entry = _rules(repo)
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, command)
    assert out["permissionDecision"] == "ask"
    assert f"#{entry}" in out["permissionDecisionReason"]
    assert line in out["permissionDecisionReason"]


def test_gh_pr_merge_is_checked(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, "gh pr merge 12 --merge")
    assert out["permissionDecision"] == "deny"


def test_allowed_emits_nothing_and_never_an_allow(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    command = "git push origin feature/x"
    # The store does answer allowed: the silence below is the hook's choice.
    entries = cl.standing_rule_entries_readonly(repo) or []
    assert ar.evaluate(command, entries, cwd=repo)["verdict"] == "allowed"
    _, stdout, err = _hook(monkeypatch, capsys, repo, command)
    assert stdout == "" and err == ""


def test_no_rule_emits_nothing(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    _, stdout, err = _hook(monkeypatch, capsys, repo, "ls -la")
    assert stdout == "" and err == ""


def test_the_strictest_segment_of_a_compound_command_decides(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, f"uv run pytest -q && {_PUSH}")
    assert out["permissionDecision"] == "deny"
    assert f"matched by `{_PUSH}`" in out["permissionDecisionReason"]


def test_a_denied_command_gets_no_search_hint(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, f"git grep foo; {_PUSH}")
    assert out["permissionDecision"] == "deny"
    assert "additionalContext" not in out


def test_the_search_hint_still_comes_when_the_check_is_silent(
    monkeypatch, capsys, repo
):
    _rules(repo)
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, "git grep some_name")
    assert "permissionDecision" not in out
    assert "cgh" in out["additionalContext"]


def test_the_check_runs_from_a_subdirectory(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    out, _, _ = _hook(monkeypatch, capsys, sub, _PUSH)
    assert out["permissionDecision"] == "deny"


# --- advise and off ------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "told"),
    [(_PUSH, "do not run it"), (_TAG, "ask the user before running it")],
)
def test_advise_tells_the_model_and_decides_nothing(
    monkeypatch, capsys, repo, command, told
):
    entry = _rules(repo)
    monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "advise")
    out, _, _ = _hook(monkeypatch, capsys, repo, command)
    assert "permissionDecision" not in out
    assert f"#{entry}" in out["additionalContext"]
    assert "cgh-rule:" in out["additionalContext"]
    assert told in out["additionalContext"]


def test_advise_is_silent_on_allowed_and_no_rule(monkeypatch, capsys, repo):
    _rules(repo)
    monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "advise")
    for command in ("git push origin feature/x", "ls -la"):
        _, stdout, _ = _hook(monkeypatch, capsys, repo, command)
        assert stdout == ""


def test_off_does_not_open_the_store(monkeypatch, capsys, repo):
    _rules(repo)
    monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "off")
    monkeypatch.setattr(
        hc, "_verdict", lambda *a, **k: pytest.fail("the check ran in off mode")
    )
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == "" and err == ""


# --- which mode applies --------------------------------------------------------


def test_the_default_is_one_constant(monkeypatch, capsys, repo):
    _rules(repo)
    assert hc.mode(repo) == hc.DEFAULT_MODE
    assert hc.DEFAULT_MODE in ("enforce", "advise")
    # Whatever the constant says today, the hook follows it.
    monkeypatch.setattr(hc, "DEFAULT_MODE", "enforce")
    out, _, _ = _hook(monkeypatch, capsys, repo, _PUSH)
    assert out["permissionDecision"] == "deny"
    monkeypatch.setattr(hc, "DEFAULT_MODE", "advise")
    out, _, _ = _hook(monkeypatch, capsys, repo, _PUSH)
    assert "permissionDecision" not in out and "additionalContext" in out


def test_repo_config_then_global_config_then_env(monkeypatch, tmp_path, repo):
    monkeypatch.setattr(hc, "DEFAULT_MODE", "advise")
    home = tmp_path / "home" / ".cgh"
    home.mkdir(parents=True)
    (home / "config.toml").write_text('[hooks]\ncheck_action = "enforce"\n')
    assert hc.mode(repo) == "enforce"
    (repo / ".cgh" / "config.toml").write_text('[hooks]\ncheck_action = "off"\n')
    assert hc.mode(repo) == "off"
    monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "Advise")
    assert hc.mode(repo) == "advise"


def test_a_config_without_the_key_says_nothing(monkeypatch, repo):
    monkeypatch.setattr(hc, "DEFAULT_MODE", "enforce")
    (repo / ".cgh" / "config.toml").write_text("[hooks]\nanswers = true\n")
    assert hc.mode(repo) == "enforce"


@pytest.mark.parametrize("source", ["env", "config"])
def test_an_unknown_mode_checks_nothing_and_says_so(monkeypatch, capsys, repo, source):
    _rules(repo)
    # A typo of enforce must not pass for a working guard.
    if source == "env":
        monkeypatch.setenv("CGH_HOOK_CHECK_ACTION", "enforced")
    else:
        (repo / ".cgh" / "config.toml").write_text(
            '[hooks]\ncheck_action = "enforced"\n'
        )
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == ""
    assert "'enforced'" in err
    assert "enforce, advise, off" in err


def test_a_mode_that_is_not_a_string_is_unknown(monkeypatch, capsys, repo):
    _rules(repo)
    (repo / ".cgh" / "config.toml").write_text("[hooks]\ncheck_action = true\n")
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == "" and "True" in err


# --- stores that say nothing ---------------------------------------------------


def test_a_store_without_rule_lines_never_reaches_the_matcher(
    monkeypatch, capsys, repo
):
    _rules(repo, body="Never push to an integration branch.")
    _enforce(monkeypatch)
    monkeypatch.setattr(
        ar, "evaluate", lambda *a, **k: pytest.fail("evaluate ran without a rule")
    )
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == "" and err == ""


def test_no_store_is_silent_and_none_is_created(monkeypatch, capsys, repo):
    _enforce(monkeypatch)
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == "" and err == ""
    assert not (repo / ".cgh" / "call_log.db").exists()


def test_the_hook_reads_the_store_without_writing_it(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    db = repo / ".cgh" / "call_log.db"
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    out, _, _ = _hook(monkeypatch, capsys, repo, _PUSH)
    assert out["permissionDecision"] == "deny"
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert cl._conns == {}  # no cached writer connection was opened


def test_a_superseded_rule_no_longer_rules(monkeypatch, capsys, repo):
    old = _rules(repo)
    _rules(repo, body="Pushing to develop is fine again.", supersedes=old)
    _enforce(monkeypatch)
    _, stdout, _ = _hook(monkeypatch, capsys, repo, _PUSH)
    assert stdout == ""


def test_an_allow_rule_of_the_calling_session_changes_nothing(
    monkeypatch, capsys, repo
):
    _rules(repo)
    _rules(repo, body="cgh-rule: allow git.push", session_id="s-1")
    _enforce(monkeypatch)
    out, _, _ = _hook(monkeypatch, capsys, repo, _PUSH, session_id="s-1")
    assert out["permissionDecision"] == "deny"


# --- the hook never blocks by failing ------------------------------------------
# Each store below holds a refuse rule that matches the command, so a working
# check would have denied: test_refused_denies_and_cites_the_rule shows it.


def _silent_within(monkeypatch, capsys, repo, command: str = _PUSH) -> str:
    started = time.monotonic()
    _, stdout, err = _hook(monkeypatch, capsys, repo, command)
    assert time.monotonic() - started < 1.0
    assert stdout == ""
    return err


def test_failure_a_store_locked_past_the_budget(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    holder = sqlite3.connect(str(repo / ".cgh" / "call_log.db"))
    try:
        # In WAL mode a plain write lock leaves readers alone; the exclusive
        # locking mode is what keeps every other connection out.
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE knowledge SET title = title")
        with pytest.raises(sqlite3.Error):
            cl.standing_rule_entries_readonly(repo)
        err = _silent_within(monkeypatch, capsys, repo)
        assert "skipped" in err and "store" in err
    finally:
        holder.rollback()
        holder.close()


def _fake_git(tmp_path, monkeypatch, script: str) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    git = bindir / "git"
    git.write_text(f"#!/bin/sh\n{script}\n")
    git.chmod(git.stat().st_mode | stat.S_IXUSR)
    if "sleep" not in script:
        # The first run of a new executable can cost more than the hook's git
        # budget (the system checks it): pay for it here, not in the hook.
        subprocess.run([str(git)], capture_output=True, timeout=10, check=False)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    # The autouse fixture pinned the branch: here the fake git answers.
    monkeypatch.setattr(ar, "current_branch", _REAL_CURRENT_BRANCH)


_REAL_CURRENT_BRANCH = ar.current_branch
_ON_BRANCH = "No commit on develop.\n\ncgh-rule: refuse git.commit on_branch=develop\n"


@pytest.mark.skipif(os.name == "nt", reason="the fake git is a shell script")
def test_failure_a_slow_git_and_a_rule_on_the_branch(
    monkeypatch, capsys, repo, tmp_path
):
    _rules(repo, body=_ON_BRANCH)
    _enforce(monkeypatch)
    _fake_git(tmp_path, monkeypatch, "echo develop")
    out, _, _ = _hook(monkeypatch, capsys, repo, "git commit -m x")
    assert out["permissionDecision"] == "deny"  # a git that answers: denied
    _fake_git(tmp_path, monkeypatch, "exec sleep 5")
    _silent_within(monkeypatch, capsys, repo, "git commit -m x")


@pytest.mark.skipif(os.name == "nt", reason="the fake git is a shell script")
def test_a_rule_that_needs_no_branch_still_denies_under_a_slow_git(
    monkeypatch, capsys, repo, tmp_path
):
    _rules(repo)
    _enforce(monkeypatch)
    _fake_git(tmp_path, monkeypatch, "exec sleep 5")
    started = time.monotonic()
    out, _, _ = _hook(monkeypatch, capsys, repo, _PUSH)
    assert time.monotonic() - started < 1.0
    assert out["permissionDecision"] == "deny"


def test_failure_a_dead_owner_changes_nothing(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    state = repo / ".cgh"
    (state / "owner.pid").write_text("999999\n")
    (state / "server.pid").write_text("999999\n")
    (state / "server.port").write_text("1\n")
    (state / "owner.sock").write_text("")
    started = time.monotonic()
    out, _, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert time.monotonic() - started < 1.0
    assert out["permissionDecision"] == "deny"
    assert err == ""


def test_failure_a_store_truncated_to_garbage(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    state = repo / ".cgh"
    for leftover in ("call_log.db-wal", "call_log.db-shm"):
        (state / leftover).unlink(missing_ok=True)
    (state / "call_log.db").write_bytes(b"not a database, " * 64)
    err = _silent_within(monkeypatch, capsys, repo)
    assert "skipped" in err and "store" in err


def test_failure_a_matcher_that_raises(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("matcher broke")

    monkeypatch.setattr(ar, "rule_matches", boom)
    err = _silent_within(monkeypatch, capsys, repo)
    assert "RuntimeError: matcher broke" in err


def test_failure_a_normaliser_that_raises(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)

    def boom(*_a, **_k):
        raise ValueError("cannot read this")

    monkeypatch.setattr(ar, "normalise", boom)
    err = _silent_within(monkeypatch, capsys, repo)
    assert "ValueError" in err


def test_failure_a_check_past_its_budget_is_abandoned(monkeypatch, capsys, repo):
    _rules(repo)
    _enforce(monkeypatch)
    real = hc._verdict

    def slow(*args, **kwargs):
        time.sleep(0.6)
        return real(*args, **kwargs)  # a deny, but too late

    monkeypatch.setattr(hc, "_verdict", slow)
    started = time.monotonic()
    _, stdout, err = _hook(monkeypatch, capsys, repo, _PUSH)
    assert time.monotonic() - started < 0.5
    assert stdout == "" and "200 ms" in err


def test_failure_mode_resolution_that_raises(monkeypatch, capsys, repo):
    _rules(repo)

    def boom(_root):
        raise OSError("config unreadable")

    monkeypatch.setattr(hc, "mode", boom)
    err = _silent_within(monkeypatch, capsys, repo)
    assert "config unreadable" in err


def test_an_unparseable_payload_is_silent(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_bash(argparse.Namespace())
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_the_budgets_are_the_ones_of_the_design():
    assert (hc.BUDGET_S, hc.STORE_BUSY_S, hc.GIT_TIMEOUT_S) == (0.2, 0.05, 0.1)


def test_the_git_budget_reaches_the_branch_read(monkeypatch, capsys, repo):
    _rules(repo, body=_ON_BRANCH)
    _enforce(monkeypatch)
    seen: list[float] = []

    def reader(_path, timeout=0.0):
        seen.append(timeout)
        return "develop"

    monkeypatch.setattr(ar, "current_branch", reader)
    out, _, _ = _hook(monkeypatch, capsys, repo, "git commit -m x")
    assert out["permissionDecision"] == "deny"
    assert seen == [hc.GIT_TIMEOUT_S]
