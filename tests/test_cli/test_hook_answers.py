# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Answer mode of the PreToolUse prechecks (CGH_HOOK_ANSWERS=1):
#              a search for a name gets its definitions, a full read of an
#              indexed source file gets its outline, and a search cgh cannot
#              answer gets silence instead of a nudge.

from __future__ import annotations

import argparse
import io
import json
import sqlite3

import pytest

from codegraph.cli import commands_hooks as h
from codegraph.cli import hook_answers as ha


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with an FTS index holding one function and a five-symbol file,
    and no live owner (callers are left out)."""
    (tmp_path / ".codegraph").mkdir()
    src = tmp_path / "app" / "billing.py"
    src.parent.mkdir()
    src.write_text("x = 1\n")
    conn = sqlite3.connect(tmp_path / ".codegraph" / "fts.db")
    conn.execute(
        "CREATE TABLE symbols (sym_id TEXT PRIMARY KEY, kind TEXT, name TEXT, "
        "file_path TEXT, start_line INTEGER, end_line INTEGER, docstring TEXT)"
    )
    rows = [("function", "charge_card", 10, 30)] + [
        ("function", f"helper_{i}", 40 + 10 * i, 45 + 10 * i) for i in range(4)
    ]
    rows.append(("method", "Invoice.settle_invoice", 90, 99))
    for i, (kind, name, sl, el) in enumerate(rows):
        conn.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, '')",
            (str(i), kind, name, str(src), sl, el),
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    monkeypatch.setattr(ha, "_callers", lambda root, name: None)
    return tmp_path


def _run(monkeypatch, capsys, func, payload: dict) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        func(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


@pytest.mark.parametrize(
    ("pattern", "names"),
    [
        ("charge_card", ["charge_card"]),
        (r"def charge_card\(", ["charge_card"]),
        (r"\bchargeCard\b", ["chargeCard"]),
        ("some random text", []),
        ("refund of charge_card failed", ["charge_card"]),
    ],
)
def test_names_in(pattern, names):
    assert ha.names_in(pattern) == names


@pytest.mark.parametrize(
    ("prog", "args", "pattern"),
    [
        ("grep", ["-rn", "charge_card", "app/"], "charge_card"),
        ("grep", ["-A", "3", "-rn", "charge_card", "."], "charge_card"),
        ("git", ["grep", "-n", "charge_card"], "charge_card"),
        ("rg", ["-e", "charge_card", "-g", "*.py"], "charge_card"),
        ("rg", ["--glob", "*.py", "charge_card"], "charge_card"),
    ],
)
def test_search_pattern(prog, args, pattern):
    assert ha.search_pattern(prog, args) == pattern


def test_grep_tool_gets_the_definition(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "charge_card"}, "cwd": str(repo)},
    )
    assert "charge_card (function)  app/billing.py:10-30" in ctx


def test_bare_method_name_finds_the_qualified_symbol(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn settle_invoice ."}, "cwd": str(repo)},
    )
    assert "Invoice.settle_invoice (method)  app/billing.py:90-99" in ctx


def test_shell_search_cgh_cannot_answer_is_silent(monkeypatch, capsys, repo):
    # No nudge either: answer mode only speaks when it has an answer.
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn unknown_name ."}, "cwd": str(repo)},
    )
    assert ctx == ""


def test_cat_after_cd_gets_the_outline(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "cd app && cat billing.py"}, "cwd": str(repo)},
    )
    assert "Outline of app/billing.py" in ctx
    assert "10-30  function  charge_card" in ctx


def test_ranged_shell_read_is_silent(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "sed -n 10,30p app/billing.py"}, "cwd": str(repo)},
    )
    assert ctx == ""


def test_full_read_gets_the_outline(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_read,
        {
            "tool_input": {"file_path": str(repo / "app" / "billing.py")},
            "cwd": str(repo),
        },
    )
    assert "Outline of app/billing.py" in ctx


def test_answers_off_keeps_the_nudge(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_HOOK_ANSWERS")
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn charge_card ."}, "cwd": str(repo)},
    )
    assert "pattern_search" in ctx
    assert "billing.py" not in ctx
