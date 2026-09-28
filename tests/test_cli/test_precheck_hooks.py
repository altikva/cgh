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


# --- Bash precheck: the same lookups run as shell commands -------------------


@pytest.mark.parametrize(
    "command",
    [
        "git grep -n resolve_import",
        "cd /repo && git grep -n resolve_import -- codegraph/",
        "grep -rn def_foo codegraph/",
        "grep -Rn def_foo .",
        "grep --recursive def_foo src",
        "LC_ALL=C rg resolve_import",
        "time ag resolve_import",
        "find . -name '*.py'",
        "sed -n '120,180p' codegraph/core/fts.py",
        "cat codegraph/indexer.py",
        "head -40 web/app.tsx",
    ],
)
def test_shell_search_hint_fires_on_code_lookups(command):
    assert h._shell_search_hint(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        # Filters another command's output, not the repo.
        "uv run pytest -q | grep FAILED",
        "gh pr checks 345 | grep -v pass",
        # rg would fire on its own; downstream of a pipe it only filters.
        "git diff --stat | rg codegraph",
        # One file that is not source: a log, a config.
        "grep ERROR .codegraph/owner.log",
        "tail -f .codegraph/owner.log",
        "cat pyproject.toml",
        "sed -n '1,40p' CHANGELOG.md",
        # Not a lookup at all.
        "git status --short",
        "uv run ruff check .",
    ],
)
def test_shell_search_hint_stays_silent(command):
    assert h._shell_search_hint(command) is None


def test_shell_search_hint_names_the_tools():
    hint = h._shell_search_hint("git grep -n resolve_import")
    assert "pattern_search" in hint and "symbol_lookup" in hint


def test_shell_search_hint_survives_unbalanced_quotes():
    # A regex with a pipe inside quotes splits badly; still a recursive grep.
    assert h._shell_search_hint('grep -rn "foo|bar" src') is not None
    assert h._shell_search_hint("grep -rn 'oops src") is not None


def test_bash_precheck_emits_context_in_a_cgh_repo(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "git grep -n foo"}, "cwd": str(tmp_path)},
    )
    assert "pattern_search" in _envelope(out)


def test_bash_precheck_is_silent_outside_a_cgh_repo(monkeypatch, capsys, tmp_path):
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "git grep -n foo"}, "cwd": str(tmp_path)},
    )
    assert out == ""


def test_bash_precheck_is_silent_on_an_ordinary_command(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "uv run pytest -q"}, "cwd": str(tmp_path)},
    )
    assert out == ""


def test_bash_precheck_is_registered_everywhere_init_and_the_plugin_install():
    # The spec list, the detection markers and the plugin's hooks.json are
    # three separate registries; a hook missing from one is silently absent.
    from pathlib import Path

    from codegraph.cli.commands_init import _CLAUDE_HOOK_MARKERS, _claude_hook_specs

    specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
    assert specs["cgh-precheck-bash"]["matcher"] == "Bash"
    assert "_hook_precheck_bash" in specs["cgh-precheck-bash"]["command"]
    assert any(m[0] == "cgh-precheck-bash" for m in _CLAUDE_HOOK_MARKERS)
    plugin = json.loads(
        (Path(__file__).parents[2] / ".claude-plugin" / "hooks.json").read_text()
    )
    commands = [
        hook["command"]
        for group in plugin["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert "cgh _hook_precheck_bash" in commands
