# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The IBM Bob lifecycle hooks: the session id reaches the model,
#              the journal turns into an automatic digest on Stop, the model
#              is nudged (or, opted in, gated) after too many calls without
#              a save, and init writes the hooks without touching the user's.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import commands_bob as bob

SID = "ses_01abc"


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


def _run(monkeypatch, handler, payload: dict) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    handler(argparse.Namespace())


def _tool(monkeypatch, root, tool, tool_input, runtime_shape=True):
    if runtime_shape:
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
        }
    else:
        payload = {"event": "PostToolUse", "tool": tool, "input": tool_input}
    _run(
        monkeypatch,
        bob.cmd_bob_tool_log,
        {**payload, "session_id": SID, "cwd": str(root)},
    )


def test_session_start_gives_the_model_its_session_id(monkeypatch, root, capsys):
    _run(monkeypatch, bob.cmd_bob_session_start, {"session_id": SID, "cwd": str(root)})
    assert SID in capsys.readouterr().out


def test_stop_writes_a_digest_of_what_changed(monkeypatch, root):
    from codegraph.state.call_log import knowledge_list

    _tool(monkeypatch, root, "write_to_file", {"path": "app/a.py"})
    _tool(monkeypatch, root, "apply_diff", {"path": "app/b.py"}, runtime_shape=False)
    _tool(monkeypatch, root, "execute_command", {"command": "uv run pytest -q"})
    _run(
        monkeypatch,
        bob.cmd_bob_stop,
        {"event": "Stop", "session_id": SID, "cwd": str(root)},
    )
    _tool(monkeypatch, root, "write_to_file", {"path": "app/c.py"})
    _run(
        monkeypatch,
        bob.cmd_bob_stop,
        {"event": "PreCompact", "session_id": SID, "cwd": str(root)},
    )

    digests = knowledge_list(tag="auto-digest", session_id=SID, repo_root=root)
    assert len(digests) == 1  # superseded, not piled up
    body = digests[0]["body"]
    assert "app/a.py" in body and "app/b.py" in body and "app/c.py" in body
    assert "uv run pytest -q" in body


def test_prompt_nudges_after_too_many_calls_without_a_save(monkeypatch, root, capsys):
    (root / ".codegraph" / "config.toml").write_text("[bob]\ncheckpoint_every = 3\n")
    for _ in range(2):
        _tool(monkeypatch, root, "read_file", {"path": "x"})
    _run(monkeypatch, bob.cmd_bob_prompt, {"session_id": SID, "cwd": str(root)})
    assert capsys.readouterr().out == ""

    _tool(monkeypatch, root, "read_file", {"path": "x"})
    _run(monkeypatch, bob.cmd_bob_prompt, {"session_id": SID, "cwd": str(root)})
    assert "checkpoint" in capsys.readouterr().out

    _tool(
        monkeypatch,
        root,
        "use_mcp_tool",
        {"server_name": "codegraph", "tool_name": "checkpoint"},
    )
    _run(monkeypatch, bob.cmd_bob_prompt, {"session_id": SID, "cwd": str(root)})
    assert capsys.readouterr().out == ""


def test_gate_is_off_unless_configured(monkeypatch, root):
    for _ in range(100):
        _tool(monkeypatch, root, "read_file", {"path": "x"})
    _run(
        monkeypatch,
        bob.cmd_bob_gate,
        {"session_id": SID, "cwd": str(root), "tool_name": "read_file"},
    )


def test_gate_blocks_once_then_lets_the_save_through(monkeypatch, root):
    (root / ".codegraph" / "config.toml").write_text("[bob]\ncheckpoint_gate = 2\n")
    for _ in range(2):
        _tool(monkeypatch, root, "read_file", {"path": "x"})
    pre = {
        "session_id": SID,
        "cwd": str(root),
        "tool_name": "read_file",
        "tool_input": {},
    }

    with pytest.raises(SystemExit) as blocked:
        _run(monkeypatch, bob.cmd_bob_gate, pre)
    assert blocked.value.code == 2
    _run(monkeypatch, bob.cmd_bob_gate, pre)  # one block per crossing
    save = {
        **pre,
        "tool_name": "use_mcp_tool",
        "tool_input": {"tool_name": "checkpoint"},
    }
    _run(monkeypatch, bob.cmd_bob_gate, save)


def test_init_writes_hooks_and_keeps_the_users(root):
    from codegraph.cli.commands_init import _install_bob_hooks

    settings = root / ".bob" / "settings.json"
    settings.parent.mkdir()
    user_hook = {
        "matcher": "^write_file$",
        "hooks": [{"type": "command", "command": "my-guard"}],
    }
    settings.write_text(
        json.dumps({"theme": "dark", "hooks": {"PreToolUse": [user_hook]}})
    )

    _install_bob_hooks(root, "cgh")
    _install_bob_hooks(root, "cgh")  # idempotent

    data = json.loads(settings.read_text())
    assert data["theme"] == "dark"
    assert data["hooks"]["PreToolUse"] == [user_hook]  # no gate unless enabled
    for event in (
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "Stop",
        "PreCompact",
    ):
        commands = [h["command"] for g in data["hooks"][event] for h in g["hooks"]]
        assert len(commands) == 1 and commands[0].startswith("cgh _bob_")

    (root / ".codegraph" / "config.toml").write_text("[bob]\ncheckpoint_gate = 60\n")
    _install_bob_hooks(root, "cgh")
    pre = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    assert user_hook in pre and any(
        "_bob_gate" in g["hooks"][0]["command"] for g in pre
    )
