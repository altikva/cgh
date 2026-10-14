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


def test_search_in_named_files_is_silent(monkeypatch, capsys, repo):
    # The agent already knows the file: the definitions would add nothing.
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {
            "tool_input": {"command": "grep -n charge_card app/billing.py"},
            "cwd": str(repo),
        },
    )
    assert ctx == ""


def test_grep_tool_on_one_file_is_silent(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {
            "tool_input": {"pattern": "charge_card", "path": "app/billing.py"},
            "cwd": str(repo),
        },
    )
    assert ctx == ""


def test_search_in_a_directory_still_answers(monkeypatch, capsys, repo):
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn charge_card app/"}, "cwd": str(repo)},
    )
    assert "charge_card (function)" in ctx


def test_heredoc_body_is_not_a_search(monkeypatch, capsys, repo):
    command = "python3 - <<'EOF'\ngrep -rn charge_card .\nEOF"
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": command}, "cwd": str(repo)},
    )
    assert ctx == ""


def test_callers_only_for_a_single_name(monkeypatch, capsys, repo):
    monkeypatch.setattr(
        ha,
        "_callers",
        lambda root, name: [{"caller": "checkout", "file": "app/x.py", "line": 3}],
    )
    single = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "charge_card"}, "cwd": str(repo)},
    )
    assert "called by checkout" in single
    alternation = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "charge_card|settle_invoice"}, "cwd": str(repo)},
    )
    assert "Invoice.settle_invoice" in alternation
    assert "called by" not in alternation


def _add_generic(repo, rows):
    """Insert get_by_id definitions: (kind, name, relative path) per row."""
    conn = sqlite3.connect(repo / ".codegraph" / "fts.db")
    for i, (kind, name, rel) in enumerate(rows):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
        conn.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, '')",
            (f"g{i}", kind, name, str(path), i * 10 + 1, i * 10 + 5),
        )
    conn.commit()
    conn.close()


_MANAGERS = [
    ("method", "get_by_id", f"app/managers/{m}_manager.py")
    for m in ("webhook", "fiscal_year", "cerfa", "donation", "payment")
]


def test_generic_name_is_capped_not_dropped(monkeypatch, capsys, repo):
    _add_generic(repo, _MANAGERS)
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "get_by_id"}, "cwd": str(repo)},
    )
    assert ctx.count("  defined  get_by_id") == ha._MAX_DEFS
    assert "(1 more definitions" in ctx
    # Callers of a name defined in many files would mix them all.
    assert "callers" not in ctx


def test_search_directory_narrows_the_definitions(monkeypatch, capsys, repo):
    _add_generic(repo, [*_MANAGERS, ("function", "get_by_id", "app/routers/x.py")])
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn get_by_id app/routers"}, "cwd": str(repo)},
    )
    assert "app/routers/x.py" in ctx
    assert "managers" not in ctx


def test_directory_without_the_definition_is_silent(monkeypatch, capsys, repo):
    # Searching where the name is not defined looks for its uses.
    _add_generic(repo, _MANAGERS)
    (repo / "app" / "routers").mkdir()
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "grep -rn get_by_id app/routers"}, "cwd": str(repo)},
    )
    assert ctx == ""


def test_file_defining_another_searched_name_comes_first(monkeypatch, capsys, repo):
    rows = [
        *_MANAGERS,
        ("method", "lock_subscription", "app/managers/payment_manager.py"),
    ]
    _add_generic(repo, rows)
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "lock_subscription|get_by_id"}, "cwd": str(repo)},
    )
    first = ctx.split("`get_by_id`:\n", 1)[1].splitlines()[0]
    assert "payment_manager.py" in first


def test_qualifier_narrows_to_its_class_file(monkeypatch, capsys, repo):
    _add_generic(repo, _MANAGERS)
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": r"WebhookManager\.get_by_id"}, "cwd": str(repo)},
    )
    assert "webhook_manager.py" in ctx
    assert "fiscal_year" not in ctx


def test_tests_are_dropped_unless_searched(monkeypatch, capsys, repo):
    rows = [
        ("function", "get_by_id", "app/managers/base.py"),
        ("function", "get_by_id", "tests/test_base.py"),
    ]
    _add_generic(repo, rows)
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "get_by_id"}, "cwd": str(repo)},
    )
    assert "app/managers/base.py" in ctx
    assert "tests/test_base.py" not in ctx
    in_tests = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "get_by_id", "path": "tests"}, "cwd": str(repo)},
    )
    assert "tests/test_base.py" in in_tests


def test_keyword_keeps_the_matching_kind(monkeypatch, capsys, repo):
    rows = [
        ("class", "Ledger", "app/models/ledger.py"),
        ("function", "Ledger", "app/factories.py"),
    ]
    _add_generic(repo, rows)
    ctx = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"tool_input": {"pattern": "class Ledger"}, "cwd": str(repo)},
    )
    assert "app/models/ledger.py" in ctx
    assert "factories" not in ctx


def test_search_parts_splits_pattern_and_paths():
    assert ha.search_parts("grep", ["-n", "-e", "x_y", "a.py", "b.py"]) == (
        "x_y",
        ["a.py", "b.py"],
    )
