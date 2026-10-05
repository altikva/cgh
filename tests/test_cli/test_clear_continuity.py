# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Continuity across a Claude Code /clear: SessionEnd turns the
#              transcript into a digest without the model, and the next
#              SessionStart (source "clear") shows a recap of that session.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli.commands_session import cmd_hook_checkpoint, cmd_hook_resume_header
from codegraph.state.transcript_digest import digest_from_transcript


def _line(kind: str, content, **extra) -> str:
    return json.dumps(
        {"type": kind, "message": {"role": kind, "content": content}, **extra}
    )


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            [
                _line("user", "<command-name>/model</command-name>"),
                _line("user", "Fix the login redirect on the admin app"),
                _line("user", "meta", isMeta=True),
                _line(
                    "assistant",
                    [
                        {"type": "text", "text": "Looking at the router."},
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": "/r/app/auth.py"},
                        },
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "uv run pytest -q"},
                        },
                    ],
                ),
                _line("user", [{"type": "tool_result", "content": "ok"}]),
                _line(
                    "assistant",
                    [{"type": "text", "text": "Fixed and tested; PR #12 is open."}],
                ),
            ]
        )
        + "\n"
    )
    return path


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


def _hook(monkeypatch, handler, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    handler(argparse.Namespace())


def test_digest_keeps_requests_edits_commands_and_the_last_answer(transcript):
    digest = digest_from_transcript(transcript)
    assert digest["requests"] == ["Fix the login redirect on the admin app"]
    assert digest["files"] == ["/r/app/auth.py"]
    assert digest["commands"] == ["uv run pytest -q"]
    assert digest["last_answer"] == "Fixed and tested; PR #12 is open."


def test_session_end_records_the_transcript_digest(monkeypatch, root, transcript):
    from codegraph.state.call_log import knowledge_list

    payload = {
        "hook_event_name": "SessionEnd",
        "reason": "clear",
        "session_id": "s1",
        "cwd": str(root),
        "transcript_path": str(transcript),
    }
    _hook(monkeypatch, cmd_hook_checkpoint, payload)
    _hook(monkeypatch, cmd_hook_checkpoint, payload)  # superseded, not piled up

    rows = knowledge_list(tag="auto-checkpoint", session_id="s1", repo_root=root)
    assert len(rows) == 1
    assert "Fix the login redirect" in rows[0]["body"]
    assert "/r/app/auth.py" in rows[0]["body"]


def test_start_after_clear_recaps_the_previous_session(
    monkeypatch, root, transcript, capsys
):
    end = {
        "hook_event_name": "SessionEnd",
        "reason": "clear",
        "session_id": "s1",
        "cwd": str(root),
        "transcript_path": str(transcript),
    }
    _hook(monkeypatch, cmd_hook_checkpoint, end)
    capsys.readouterr()

    _hook(
        monkeypatch,
        cmd_hook_resume_header,
        {"source": "clear", "session_id": "s2", "cwd": str(root)},
    )
    out = capsys.readouterr().out
    assert "Before this /clear" in out
    assert "Fix the login redirect" in out
    assert "cgh session id: s2" in out


def test_a_plain_start_shows_no_recap(monkeypatch, root, transcript, capsys):
    end = {
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
        "cwd": str(root),
        "transcript_path": str(transcript),
    }
    _hook(monkeypatch, cmd_hook_checkpoint, end)
    capsys.readouterr()
    _hook(
        monkeypatch,
        cmd_hook_resume_header,
        {"source": "startup", "session_id": "s2", "cwd": str(root)},
    )
    assert "Before this /clear" not in capsys.readouterr().out
