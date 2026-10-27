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
    # Never the developer's own ~/.codegraph/config.toml.
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home-codegraph")
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


def test_answers_from_config_reach_a_running_session(monkeypatch, capsys, repo):
    # A session started without the variable still gets answers once the
    # repo's config turns them on: hooks read the file on every call.
    monkeypatch.delenv("CGH_HOOK_ANSWERS")
    grep = {"tool_input": {"command": "grep -rn charge_card ."}, "cwd": str(repo)}
    assert "billing.py" not in _run(monkeypatch, capsys, h.cmd_hook_precheck_bash, grep)
    cfg = repo / ".codegraph" / "config.toml"
    cfg.write_text("[codegraph]\nmax_file_size_kb = 500\n\n[hooks]\nanswers = true\n")
    assert "billing.py" in _run(monkeypatch, capsys, h.cmd_hook_precheck_bash, grep)
    # The variable still wins, both ways.
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "0")
    assert "billing.py" not in _run(monkeypatch, capsys, h.cmd_hook_precheck_bash, grep)


def test_answers_config_order_and_bad_files(monkeypatch, tmp_path, repo):
    monkeypatch.delenv("CGH_HOOK_ANSWERS")
    home = tmp_path / "home-codegraph"
    home.mkdir()
    (home / "config.toml").write_text("[hooks]\nanswers = true\n")
    assert ha.enabled(repo)  # the global default applies
    (repo / ".codegraph" / "config.toml").write_text("[hooks]\nanswers = false\n")
    assert not ha.enabled(repo)  # the repo overrides it
    (repo / ".codegraph" / "config.toml").write_text("[hooks\nbroken")
    assert ha.enabled(repo)  # an unreadable repo file says nothing
    (home / "config.toml").write_text('[hooks]\nanswers = "yes"\n')
    assert not ha.enabled(repo)  # only a real boolean counts
    assert not ha.enabled(tmp_path / "elsewhere")


# --- full Read of a large text file -------------------------------------------


def _read(monkeypatch, capsys, repo, path, **extra) -> dict:
    """Run the Read hook on ``path`` and return its JSON output ({} when silent)."""
    payload = {
        "tool_input": {"file_path": str(path), **extra},
        "cwd": str(repo),
        "session_id": "sess-1",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_read(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else {}


def _denied(out: dict) -> bool:
    return out.get("permissionDecision") == "deny"


def _big_py(repo):
    src = repo / "app" / "billing.py"
    src.write_text("".join(f"value_{i} = {i}  # padding\n" for i in range(1700)))
    assert src.stat().st_size > 32 * 1024
    return src


def _big_txt(repo, name="notes.txt", size=40 * 1024):
    path = repo / name
    line = "plain words with nothing to outline in them\n"
    path.write_text(line * (size // len(line) + 1))
    return path


def test_full_read_of_large_indexed_file_is_denied_with_outline(
    monkeypatch, capsys, repo
):
    out = _read(monkeypatch, capsys, repo, _big_py(repo))
    assert _denied(out)
    reason = out["permissionDecisionReason"]
    assert "Full Read of app/billing.py" in reason
    assert "10-30  function  charge_card" in reason
    assert "offset/limit" in reason
    assert reason.count("offset/limit") == 1


def test_full_read_of_large_unindexed_text_is_denied_with_anchors(
    monkeypatch, capsys, repo
):
    out = _read(monkeypatch, capsys, repo, _big_txt(repo))
    assert _denied(out)
    reason = out["permissionDecisionReason"]
    assert "Full Read of notes.txt (40 KB)" in reason
    assert "lines along the file" in reason
    assert "  1  plain words" in reason
    assert len([ln for ln in reason.splitlines() if "plain words" in ln]) == 10


def test_unindexed_patch_lists_its_files(monkeypatch, capsys, repo):
    path = repo / "change.patch"
    body = "".join(
        f"diff --git a/f{i}.py b/f{i}.py\n" + "+an added line of the patch\n" * 400
        for i in range(5)
    )
    path.write_text(body)
    reason = _read(monkeypatch, capsys, repo, path)["permissionDecisionReason"]
    assert "its parts" in reason
    assert "  1  diff --git a/f0.py b/f0.py" in reason
    assert "  402  diff --git a/f1.py b/f1.py" in reason


def test_retry_right_after_a_deny_goes_through(monkeypatch, capsys, repo):
    path = _big_txt(repo)
    assert _denied(_read(monkeypatch, capsys, repo, path))
    assert _read(monkeypatch, capsys, repo, path) == {}
    # The retry cleared the record: a later full Read is answered again.
    assert _denied(_read(monkeypatch, capsys, repo, path))


def test_ranged_read_is_not_denied(monkeypatch, capsys, repo):
    path = _big_txt(repo)
    assert not _denied(_read(monkeypatch, capsys, repo, path, offset=1, limit=50))
    assert not _denied(_read(monkeypatch, capsys, repo, path, limit=50))


def test_file_under_32_kb_is_not_denied(monkeypatch, capsys, repo):
    out = _read(monkeypatch, capsys, repo, _big_txt(repo, size=20 * 1024))
    assert not _denied(out)


@pytest.mark.parametrize("name", ["shot.png", "spec.pdf", "blob.bin"])
def test_binaries_are_not_denied(monkeypatch, capsys, repo, name):
    path = repo / name
    path.write_bytes(b"\x89PNG\0\0" + b"\0\1\2\3" * 10_000)
    assert not _denied(_read(monkeypatch, capsys, repo, path))


def test_answers_off_keeps_the_hint(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "0")
    out = _read(monkeypatch, capsys, repo, _big_txt(repo))
    assert not _denied(out)
    assert "offset/limit" in out.get("additionalContext", "")


def test_no_session_id_is_not_denied(monkeypatch, capsys, repo):
    path = _big_txt(repo)
    payload = {"tool_input": {"file_path": str(path)}, "cwd": str(repo)}
    ctx = _run(monkeypatch, capsys, h.cmd_hook_precheck_read, payload)
    assert "offset/limit" in ctx


def test_large_indexed_markdown_gets_its_section_ranges(monkeypatch, capsys, repo):
    doc = repo / "GUIDE.md"
    doc.write_text("# Guide\n" + "text of the guide\n" * 2500)
    conn = sqlite3.connect(repo / ".codegraph" / "fts.db")
    for i, (name, sl, el) in enumerate([("Setup", 2, 900), ("Usage", 901, 2501)]):
        conn.execute(
            "INSERT INTO symbols VALUES (?, 'md_section', ?, ?, ?, ?, '')",
            (f"md{i}", name, str(doc), sl, el),
        )
    conn.commit()
    conn.close()
    reason = _read(monkeypatch, capsys, repo, doc)["permissionDecisionReason"]
    assert "Main parts: Setup 2-900, Usage 901-2501." in reason
    assert "[cgh hook]" not in reason


def test_lock_file_gets_anchors_not_its_repeated_keys(monkeypatch, capsys, repo):
    path = repo / "uv.lock"
    block = (
        '[[package]]\nname = "pkg"\nversion = "1.0"\n'
        'wheels = [\n    { url = "https://files.example/pkg-1.0.whl" },\n]\n\n'
    )
    path.write_text("version = 1\n" + block * 600)
    reason = _read(monkeypatch, capsys, repo, path)["permissionDecisionReason"]
    assert "lines along the file" in reason
    assert "its parts" not in reason
    assert "heading-like" not in reason
    assert len([ln for ln in reason.splitlines() if ln.startswith("  ")]) == 10
