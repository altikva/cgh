# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Grep/Read PreToolUse prechecks must deliver their nudge
#              through hookSpecificOutput.additionalContext on stdout, not
#              stderr. For a PreToolUse hook, plain exit-0 output never
#              reaches the model (only UserPromptSubmit / UserPromptExpansion
#              / SessionStart get that), so a stderr nudge fired into the
#              void. These tests pin the JSON envelope for the firing cases
#              and silence for the advisory skip cases.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import commands_hooks as h


def _run(monkeypatch, capsys, func, payload: dict) -> str:
    """Feed a hook payload on stdin, run the precheck, return captured
    stdout. The prechecks always sys.exit(0)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        func(argparse.Namespace())
    assert exc.value.code == 0
    return capsys.readouterr().out


def _envelope(out: str) -> str:
    """Assert the output is a PreToolUse additionalContext envelope and
    return the advisory text."""
    doc = json.loads(out)
    hso = doc["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    return hso["additionalContext"]


def test_emit_nudge_is_pretooluse_additionalcontext(capsys):
    with pytest.raises(SystemExit) as exc:
        h._emit_nudge("hello world")
    assert exc.value.code == 0
    assert _envelope(capsys.readouterr().out) == "hello world"


def test_grep_bare_identifier_emits_context(monkeypatch, capsys):
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "user_manager"}},
    )
    ctx = _envelope(out)
    assert "user_manager" in ctx
    assert "symbol_lookup" in ctx


def test_grep_regex_pattern_is_silent(monkeypatch, capsys):
    # A metachar means the caller wants a real text search: no nudge.
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "foo|bar"}},
    )
    assert out == ""


def test_grep_too_short_pattern_is_silent(monkeypatch, capsys):
    out = _run(
        monkeypatch, capsys, h.cmd_hook_precheck_grep, {"tool_input": {"pattern": "ab"}}
    )
    assert out == ""


def test_read_without_index_is_silent(monkeypatch, capsys, tmp_path):
    # No .codegraph/fts.db up-tree: nothing to suggest.
    f = tmp_path / "module.py"
    f.write_text("x = 1\n")
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_read,
        {"tool_input": {"file_path": str(f)}, "cwd": str(tmp_path)},
    )
    assert out == ""


def test_read_sliced_is_silent(monkeypatch, capsys, tmp_path):
    # A sliced read means the caller already knows the range they want.
    f = tmp_path / "module.py"
    f.write_text("x = 1\n")
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_read,
        {"tool_input": {"file_path": str(f), "offset": 1, "limit": 20}},
    )
    assert out == ""


def test_bad_stdin_is_silent(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_grep(argparse.Namespace())
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""
