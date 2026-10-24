# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The static PreToolUse nudges (Grep on a bare identifier, a
#              shell search for code) give the full paragraph once per
#              session and a one-line reminder afterwards, since every
#              nudge stays in the transcript and is resent on each request.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import commands_hooks as h


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.delenv("CGH_HOOK_ANSWERS", raising=False)
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home-codegraph")
    return tmp_path


def _ctx(monkeypatch, capsys, func, payload: dict) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit):
        func(argparse.Namespace())
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_grep_hint_full_once_then_short(monkeypatch, capsys, repo):
    payload = {
        "tool_input": {"pattern": "charge_card"},
        "cwd": str(repo),
        "session_id": "s1",
    }
    first = _ctx(monkeypatch, capsys, h.cmd_hook_precheck_grep, payload)
    second = _ctx(monkeypatch, capsys, h.cmd_hook_precheck_grep, payload)
    assert "symbol_lookup('charge_card')" in first
    assert second.startswith("[cgh hook] Grep on a bare identifier again")
    assert len(second) < len(first) / 2


def test_another_session_gets_the_full_hint(monkeypatch, capsys, repo):
    base = {"tool_input": {"pattern": "charge_card"}, "cwd": str(repo)}
    _ctx(monkeypatch, capsys, h.cmd_hook_precheck_grep, {**base, "session_id": "a"})
    other = _ctx(
        monkeypatch, capsys, h.cmd_hook_precheck_grep, {**base, "session_id": "b"}
    )
    assert "symbol_lookup('charge_card')" in other


def test_no_session_id_always_full(monkeypatch, capsys, repo):
    payload = {"tool_input": {"pattern": "charge_card"}, "cwd": str(repo)}
    _ctx(monkeypatch, capsys, h.cmd_hook_precheck_grep, payload)
    again = _ctx(monkeypatch, capsys, h.cmd_hook_precheck_grep, payload)
    assert "symbol_lookup('charge_card')" in again


def test_shell_search_kinds_are_tracked_apart(monkeypatch, capsys, repo):
    def bash(cmd: str) -> str:
        payload = {"tool_input": {"command": cmd}, "cwd": str(repo), "session_id": "s"}
        return _ctx(monkeypatch, capsys, h.cmd_hook_precheck_bash, payload)

    assert "pattern_search(pattern, glob?)" in bash("git grep -n foo")
    assert bash("git grep -n bar").startswith("[cgh hook] `git grep` again")
    # A different searcher is a different hint: full the first time.
    assert "pattern_search(pattern, glob?)" in bash("rg foo")
