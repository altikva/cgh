# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-22
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The token-saving hook hints: a narrower form for chatty Bash
#              commands, and a once-per-threshold compaction hint for a
#              large session transcript. Both advisory, both can be turned
#              off.

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
    for env in ("CGH_HOOK_ANSWERS", "CGH_HOOK_BASH_ADVICE", "CGH_HOOK_COMPACT_HINT"):
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
