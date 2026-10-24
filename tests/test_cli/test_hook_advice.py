# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-22
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The token-saving hook hints: a narrower form for chatty Bash
#              commands, and a once-per-threshold compaction hint for a
#              large session transcript, a hint before a full Read of a
#              large file cgh does not index, and heredoc bodies kept out
#              of the search nudge. All advisory, all can be turned off.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import commands_hooks as h
from codegraph.cli import hook_advice as a


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home-cg")
    for env in (
        "CGH_HOOK_ANSWERS",
        "CGH_HOOK_BASH_ADVICE",
        "CGH_HOOK_COMPACT_HINT",
        "CGH_HOOK_READ_ADVICE",
    ):
        monkeypatch.delenv(env, raising=False)


def _run(monkeypatch, capsys, func, payload: dict) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        func(argparse.Namespace())
    assert exc.value.code == 0
    return capsys.readouterr().out


# --- Bash advice -------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest", "-q"),
        ("uv run pytest tests/test_cli", "-q"),
        ("python -m pytest -v", "-q"),
        ("git log", "-n 20"),
        ("git log -p src/", "-n 20"),
        ("npm install", "--silent"),
        ("pnpm build", "--silent"),
        ("npm run build", "--silent"),
        ("yarn", "--silent"),
        ("find .", "-maxdepth"),
        ("find . -type f", "-maxdepth"),
        ("ls -R", "tree -L 2"),
        ("ls -laR src", "tree -L 2"),
        ("docker logs api", "--tail 50"),
        ("docker compose logs web", "--tail 50"),
        ("kubectl logs -n prod pod/api", "--tail 50"),
        ("cd /tmp && git log", "-n 20"),
    ],
)
def test_bash_advice_fires_on_chatty_commands(command, expected, tmp_path):
    hint = a.bash_advice(command, tmp_path)
    assert hint is not None and expected in hint
    assert len(hint.encode()) < 300


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q",
        "pytest -xq tests",
        "pytest --tb=short",
        "pytest 2>&1 | tail -20",
        "pytest > out.log 2>&1",
        "git log --oneline",
        "git log -n 5",
        "git log -3",
        "git log main..HEAD",
        "git log --since=2.weeks",
        "npm install --silent",
        "pnpm install --reporter=silent",
        "npm run test",
        "npm install | tail -5",
        "find src -type f",
        "find . -name '*.py'",
        "find . -maxdepth 2",
        "ls -la",
        "docker logs --tail 50 api",
        "docker logs -n 20 api",
        "kubectl logs --tail=100 -n prod pod/api",
        "cat pyproject.toml",
        "git status --short",
        # A heredoc body is data, not commands.
        "cat <<'EOF' > x.sh\npytest\nEOF",
    ],
)
def test_bash_advice_stays_silent_on_narrow_commands(command, tmp_path):
    assert a.bash_advice(command, tmp_path) is None


def test_bash_advice_on_cat_depends_on_size(tmp_path):
    (tmp_path / "small.log").write_text("x" * 100)
    (tmp_path / "big.log").write_text("x" * 64 * 1024)
    (tmp_path / "big.py").write_text("x" * 64 * 1024)
    assert a.bash_advice("cat small.log", tmp_path) is None
    hint = a.bash_advice("cat big.log", tmp_path)
    assert hint is not None and "head -50" in hint and "64 KB" in hint
    # An indexed source file is the Read precheck's business.
    assert a.bash_advice("cat big.py", tmp_path) is None
    assert a.bash_advice("cat big.log | head -5", tmp_path) is None


def test_bash_precheck_delivers_the_advice(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "git log"}, "cwd": str(tmp_path)},
    )
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "git log --oneline -n 20" in ctx


def test_bash_precheck_search_hint_wins_over_advice(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "find . -name '*.py'"}, "cwd": str(tmp_path)},
    )
    assert (
        "search_symbols" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    )


def test_bash_advice_opt_out_by_env(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setenv("CGH_HOOK_BASH_ADVICE", "0")
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_bash,
        {"tool_input": {"command": "git log"}, "cwd": str(tmp_path)},
    )
    assert out == ""


def test_bash_advice_opt_out_by_config(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "fts.db").write_bytes(b"")
    assert a.bash_advice_enabled(tmp_path)
    (tmp_path / ".codegraph" / "config.toml").write_text(
        "[hooks]\nbash_advice = false\n"
    )
    assert not a.bash_advice_enabled(tmp_path)


def test_global_config_opt_out(tmp_path):
    home = tmp_path / "home-cg"
    home.mkdir()
    (home / "config.toml").write_text("[hooks]\ncompact_hint = false\n")
    assert not a.compact_hint_enabled(tmp_path)
    assert a.bash_advice_enabled(tmp_path)


# --- compaction hint ---------------------------------------------------------


def _transcript(tmp_path, size: int):
    t = tmp_path / "session.jsonl"
    t.write_bytes(b"x" * size)
    return t


def test_compact_hint_silent_below_the_first_threshold(tmp_path):
    t = _transcript(tmp_path, 100_000)
    assert a.compact_hint(tmp_path, "s1", t) is None


def test_compact_hint_fires_once_per_threshold(tmp_path):
    first = a.COMPACT_THRESHOLDS[0]
    t = _transcript(tmp_path, first + 10)
    hint = a.compact_hint(tmp_path, "s1", t)
    assert hint is not None and "/compact" in hint and "/clear" in hint
    assert len(hint.encode()) < 300
    # The same threshold never fires twice, however many prompts follow.
    assert a.compact_hint(tmp_path, "s1", t) is None
    with t.open("ab") as fh:
        fh.write(b"y" * 1000)
    assert a.compact_hint(tmp_path, "s1", t) is None
    # The next threshold fires once.
    with t.open("ab") as fh:
        fh.write(b"y" * (a.COMPACT_THRESHOLDS[1] - t.stat().st_size))
    assert "1.5 MB" in a.compact_hint(tmp_path, "s1", t)
    assert a.compact_hint(tmp_path, "s1", t) is None


def test_compact_hint_jump_over_two_thresholds_fires_once(tmp_path):
    t = _transcript(tmp_path, a.COMPACT_THRESHOLDS[1] + 1)
    assert "1.5 MB" in a.compact_hint(tmp_path, "s1", t)
    assert a.compact_hint(tmp_path, "s1", t) is None


def test_compact_hint_is_per_session(tmp_path):
    t = _transcript(tmp_path, a.COMPACT_THRESHOLDS[0] + 1)
    assert a.compact_hint(tmp_path, "s1", t) is not None
    assert a.compact_hint(tmp_path, "s2", t) is not None


def test_compact_hint_counts_from_the_last_compaction(tmp_path):
    first = a.COMPACT_THRESHOLDS[0]
    t = _transcript(tmp_path, first + 1)
    assert a.compact_hint(tmp_path, "s1", t) is not None
    with t.open("ab") as fh:
        fh.write(b'\n{"type":"system","subtype":"compact_boundary"}\n')
    # Right after a compaction the live part is small: silent.
    assert a.compact_hint(tmp_path, "s1", t) is None
    with t.open("ab") as fh:
        fh.write(b"z" * (first + 10))
    # It grew past the first threshold again since the compaction.
    assert a.compact_hint(tmp_path, "s1", t) is not None


def test_compact_hook_emits_to_user_and_model(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    t = _transcript(tmp_path, a.COMPACT_THRESHOLDS[0] + 1)
    payload = {"session_id": "abc", "transcript_path": str(t), "cwd": str(tmp_path)}
    out = _run(monkeypatch, capsys, h.cmd_hook_compact_hint, payload)
    doc = json.loads(out)
    assert "/compact" in doc["systemMessage"]
    assert doc["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert (tmp_path / ".codegraph" / "sessions" / "compact-abc.json").is_file()
    assert _run(monkeypatch, capsys, h.cmd_hook_compact_hint, payload) == ""


def test_compact_hook_opt_out(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setenv("CGH_HOOK_COMPACT_HINT", "0")
    t = _transcript(tmp_path, a.COMPACT_THRESHOLDS[0] + 1)
    payload = {"session_id": "abc", "transcript_path": str(t), "cwd": str(tmp_path)}
    assert _run(monkeypatch, capsys, h.cmd_hook_compact_hint, payload) == ""


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_id": "abc"},
        {"session_id": "abc", "transcript_path": "/nonexistent/t.jsonl"},
    ],
)
def test_compact_hook_is_silent_on_missing_data(monkeypatch, capsys, tmp_path, payload):
    (tmp_path / ".codegraph").mkdir()
    payload = {**payload, "cwd": str(tmp_path)}
    assert _run(monkeypatch, capsys, h.cmd_hook_compact_hint, payload) == ""


def test_compact_hook_is_silent_outside_a_cgh_repo(monkeypatch, capsys, tmp_path):
    t = _transcript(tmp_path, a.COMPACT_THRESHOLDS[0] + 1)
    payload = {"session_id": "abc", "transcript_path": str(t), "cwd": str(tmp_path)}
    assert _run(monkeypatch, capsys, h.cmd_hook_compact_hint, payload) == ""


def test_compact_hook_is_registered_in_init_and_the_plugin():
    from pathlib import Path

    from codegraph.cli.commands_init import _CLAUDE_HOOK_MARKERS, _claude_hook_specs

    specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
    assert specs["cgh-compact-hint"]["event"] == "UserPromptSubmit"
    assert any(m[0] == "cgh-compact-hint" for m in _CLAUDE_HOOK_MARKERS)
    plugin = json.loads(
        (Path(__file__).parents[2] / ".claude-plugin" / "hooks.json").read_text()
    )
    commands = [
        hook["command"]
        for group in plugin["hooks"]["UserPromptSubmit"]
        for hook in group["hooks"]
    ]
    assert "cgh _hook_compact_hint" in commands


# --- Read advice -------------------------------------------------------------


@pytest.fixture
def indexed_repo(tmp_path):
    """A cgh repo whose FTS index holds one indexed source file."""
    import sqlite3

    (tmp_path / ".codegraph").mkdir()
    src = tmp_path / "billing.py"
    src.write_text("x = 1\n" * 8000)
    conn = sqlite3.connect(tmp_path / ".codegraph" / "fts.db")
    conn.execute(
        "CREATE TABLE symbols (sym_id TEXT PRIMARY KEY, kind TEXT, name TEXT, "
        "file_path TEXT, start_line INTEGER, end_line INTEGER, docstring TEXT)"
    )
    for i in range(6):
        conn.execute(
            "INSERT INTO symbols VALUES (?, 'function', ?, ?, ?, ?, '')",
            (str(i), f"fn_{i}", str(src.resolve()), i * 10 + 1, i * 10 + 5),
        )
    conn.commit()
    conn.close()
    return tmp_path


def _read(monkeypatch, capsys, repo, name, **extra) -> str:
    payload = {"tool_input": {"file_path": str(repo / name), **extra}, "cwd": str(repo)}
    return _run(monkeypatch, capsys, h.cmd_hook_precheck_read, payload)


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_full_read_of_a_large_unindexed_file_gets_a_hint(
    monkeypatch, capsys, indexed_repo
):
    (indexed_repo / "data.json").write_text('{"k": 1},\n' * 5000)
    hint = _context(_read(monkeypatch, capsys, indexed_repo, "data.json"))
    assert "data.json" in hint and "48 KB" in hint and "5,000 lines" in hint
    assert "offset/limit" in hint and "Grep" in hint and "head" in hint
    assert len(hint.encode()) < 300


def test_read_hint_stays_short_for_a_long_name(monkeypatch, capsys, indexed_repo):
    name = "x" * 200 + ".log"
    (indexed_repo / name).write_text("line\n" * 10000)
    hint = _context(_read(monkeypatch, capsys, indexed_repo, name))
    assert len(hint.encode()) < 300


def test_ranged_read_of_a_large_file_is_silent(monkeypatch, capsys, indexed_repo):
    (indexed_repo / "data.json").write_text("{}\n" * 20000)
    assert _read(monkeypatch, capsys, indexed_repo, "data.json", limit=50) == ""
    assert _read(monkeypatch, capsys, indexed_repo, "data.json", offset=10) == ""


def test_read_of_a_small_unindexed_file_is_silent(monkeypatch, capsys, indexed_repo):
    (indexed_repo / "small.json").write_text("{}\n" * 100)
    assert _read(monkeypatch, capsys, indexed_repo, "small.json") == ""


def test_read_of_a_large_indexed_file_gets_no_size_hint(
    monkeypatch, capsys, indexed_repo
):
    out = _read(monkeypatch, capsys, indexed_repo, "billing.py")
    assert "not indexed" not in out


def test_read_of_a_large_indexed_file_in_answer_mode_gets_the_outline_only(
    monkeypatch, capsys, indexed_repo
):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    out = _read(monkeypatch, capsys, indexed_repo, "billing.py")
    assert "not indexed" not in out


def test_read_advice_opt_out_by_env(monkeypatch, capsys, indexed_repo):
    (indexed_repo / "data.json").write_text("{}\n" * 20000)
    monkeypatch.setenv("CGH_HOOK_READ_ADVICE", "0")
    assert _read(monkeypatch, capsys, indexed_repo, "data.json") == ""


def test_read_advice_opt_out_by_config(monkeypatch, capsys, indexed_repo):
    (indexed_repo / "data.json").write_text("{}\n" * 20000)
    (indexed_repo / ".codegraph" / "config.toml").write_text(
        "[hooks]\nread_advice = false\n"
    )
    assert _read(monkeypatch, capsys, indexed_repo, "data.json") == ""


def test_read_advice_skips_the_line_count_on_a_huge_file(tmp_path, monkeypatch):
    f = tmp_path / "big.log"
    f.write_text("line\n" * 20000)
    monkeypatch.setattr(a, "_COUNT_LINES_MAX", 1024)
    hint = a.read_advice(f)
    assert hint is not None and "lines" not in hint.split("is not")[0]


def _add_symbols(repo, path, rows, kind="function"):
    import sqlite3

    conn = sqlite3.connect(repo / ".codegraph" / "fts.db")
    for name, start, end in rows:
        conn.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, '')",
            (
                f"{path.name}:{name}:{start}",
                kind,
                name,
                str(path.resolve()),
                start,
                end,
            ),
        )
    conn.commit()
    conn.close()


def test_unindexed_threshold_is_twelve_kb(monkeypatch, capsys, indexed_repo):
    (indexed_repo / "mid.json").write_text("{}\n" * 4500)  # 13 KB
    assert "mid.json" in _context(_read(monkeypatch, capsys, indexed_repo, "mid.json"))
    (indexed_repo / "small.json").write_text("{}\n" * 3500)  # 10 KB
    assert _read(monkeypatch, capsys, indexed_repo, "small.json") == ""


def test_full_read_of_a_large_indexed_file_gets_its_ranges(
    monkeypatch, capsys, indexed_repo
):
    hint = _context(_read(monkeypatch, capsys, indexed_repo, "billing.py"))
    assert hint.startswith("[cgh hook] billing.py (46 KB): fn_0 1-5, fn_1 11-15")
    assert "fn_5 51-55" in hint and "offset/limit" in hint
    assert "\n" not in hint and len(hint.encode()) < 300


def test_ranges_hint_keeps_the_largest_symbols_under_300_bytes(
    monkeypatch, capsys, indexed_repo
):
    src = indexed_repo / "big.py"
    src.write_text("pass\n" * 6000)
    rows = [
        (f"handler_with_a_rather_long_name_{i:02d}", i * 100 + 1, i * 100 + 10 + i)
        for i in range(40)
    ]
    _add_symbols(indexed_repo, src, rows)
    hint = _context(_read(monkeypatch, capsys, indexed_repo, "big.py"))
    assert len(hint.encode()) < 300
    assert "more." in hint and "_39 3901-3949" in hint and "_00 " not in hint


def test_ranges_hint_names_the_children_of_a_lone_top_symbol():
    rows = [
        ("Index", 1, 300),
        ("parse", 10, 80),
        ("helper", 20, 30),
        ("write", 90, 299),
    ]
    assert a._main_symbols(rows) == [("parse", 10, 80), ("write", 90, 299)]


def test_ranges_hint_is_silent_for_ranged_and_small_reads(
    monkeypatch, capsys, indexed_repo
):
    assert _read(monkeypatch, capsys, indexed_repo, "billing.py", limit=40) == ""
    small = indexed_repo / "small.py"
    small.write_text("pass\n" * 1000)  # 5 KB
    _add_symbols(
        indexed_repo, small, [(f"f{i}", i * 10 + 1, i * 10 + 5) for i in range(6)]
    )
    out = _read(monkeypatch, capsys, indexed_repo, "small.py")
    assert "Read a range with offset/limit." not in out


def test_answer_mode_outline_is_not_doubled_by_the_ranges_hint(
    monkeypatch, capsys, indexed_repo
):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    hint = _context(_read(monkeypatch, capsys, indexed_repo, "billing.py"))
    assert hint.startswith("[cgh] Outline of billing.py")
    assert "Read a range with offset/limit." not in hint


def test_answer_mode_without_an_outline_gets_the_ranges_hint(
    monkeypatch, capsys, indexed_repo
):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    doc = indexed_repo / "GUIDE.md"
    doc.write_text("text\n" * 4000)
    rows = [("Guide", 1, 4000), ("Install", 3, 900), ("Usage", 901, 4000)]
    _add_symbols(indexed_repo, doc, rows, kind="md_section")
    hint = _context(_read(monkeypatch, capsys, indexed_repo, "GUIDE.md"))
    assert hint == (
        "[cgh hook] GUIDE.md (19 KB): Install 3-900, Usage 901-4000. "
        "Read a range with offset/limit."
    )


def test_ranges_hint_opt_out(monkeypatch, capsys, indexed_repo):
    monkeypatch.setenv("CGH_HOOK_READ_ADVICE", "0")
    out = _read(monkeypatch, capsys, indexed_repo, "billing.py")
    assert "Read a range with offset/limit." not in out
    assert "indexed by cgh (6 symbols)" in _context(out)


# --- heredocs are data, not searches -----------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF' > notes.md\nrun grep foo over the tree\ngit grep -n bar\nEOF",
        "cat <<EOF > script.sh\ngrep -rn foo src\nrg bar\nEOF",
        "python3 - <<'PY'\nimport os  # find . -name x\nPY",
    ],
)
def test_search_hint_ignores_heredoc_bodies(command):
    assert h._shell_search_hint(command) is None


def test_search_hint_still_fires_after_a_heredoc():
    command = "cat <<'EOF' > notes.md\nhello\nEOF\ngit grep -n foo"
    assert h._shell_search_hint(command) is not None


def test_bash_precheck_is_silent_on_a_heredoc_with_grep(monkeypatch, capsys, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    payload = {
        "tool_input": {"command": "cat <<'EOF' > notes.md\ngrep foo\nEOF"},
        "cwd": str(tmp_path),
    }
    assert _run(monkeypatch, capsys, h.cmd_hook_precheck_bash, payload) == ""
