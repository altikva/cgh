# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The context steward (CGH_CONTEXT_STEWARD=1): the context gauge
#              after each tool call (checkpoint, then relay request), the
#              sub-agent brief enriched with cgh's locations, and the opt-in
#              install of both hooks.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import commands_hooks as h
from codegraph.cli import context_steward as cs
from codegraph.core.fts import get_fts_conn, upsert_symbol


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with an FTS index holding one function, the steward on, and
    thresholds of 100k (checkpoint) and 150k (relay)."""
    conn = get_fts_conn(tmp_path)
    src = tmp_path / "app" / "billing.py"
    src.parent.mkdir()
    src.write_text("x = 1\n")
    upsert_symbol(conn, "b::charge_card", "function", "charge_card", str(src), 10, 30)
    conn.commit()
    conn.close()
    monkeypatch.setenv("CGH_CONTEXT_STEWARD", "1")
    monkeypatch.setenv("CGH_CONTEXT_CHECKPOINT", "100000")
    monkeypatch.setenv("CGH_CONTEXT_RELAY", "150000")
    return tmp_path


def _transcript(path, *sizes, sidechain_last=None):
    """A transcript whose main-chain turns read ``sizes`` tokens each."""
    lines = ['{"cut line of an earlier entry'] + [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": s - 110,
                        "cache_creation_input_tokens": 100,
                    }
                },
            }
        )
        for s in sizes
    ]
    if sidechain_last is not None:
        lines.append(
            json.dumps(
                {
                    "isSidechain": True,
                    "message": {"usage": {"input_tokens": sidechain_last}},
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")
    return path


def _run(monkeypatch, capsys, func, payload: dict) -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        func(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else {}


def test_context_size_reads_the_last_main_chain_turn(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", 50_000, 120_000, sidechain_last=999_999)
    assert cs.context_size(t) == 120_000


def test_gauge_below_the_checkpoint_says_nothing(repo):
    t = _transcript(repo / "t.jsonl", 90_000)
    assert cs.gauge_message(repo, "s1", t) is None


def test_checkpoint_is_asked_once(repo):
    t = _transcript(repo / "t.jsonl", 110_000)
    first = cs.gauge_message(repo, "s1", t)
    assert "checkpoint(session_id='s1'" in first
    assert cs.gauge_message(repo, "s1", t) is None


def test_relay_asks_for_a_stop_and_drops_a_request(repo):
    t = _transcript(repo / "t.jsonl", 160_000)
    msg = cs.gauge_message(repo, "s1", t)
    assert cs.RELAY_READY in msg
    request = json.loads(
        (repo / ".codegraph" / "steward" / "relay-request.json").read_text()
    )
    assert request["session_id"] == "s1"
    assert request["context_tokens"] == 160_000
    # Said again only after another 25k of growth.
    assert cs.gauge_message(repo, "s1", _transcript(t, 170_000)) is None
    assert cs.RELAY_READY in cs.gauge_message(repo, "s1", _transcript(t, 186_000))


def test_gauge_hook_emits_post_tool_use_context(monkeypatch, capsys, repo):
    t = _transcript(repo / "t.jsonl", 110_000)
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_context_gauge,
        {"session_id": "s1", "transcript_path": str(t), "cwd": str(repo)},
    )
    assert out["hookEventName"] == "PostToolUse"
    assert "110k tokens" in out["additionalContext"]


def test_gauge_ignores_sub_agent_calls(monkeypatch, capsys, repo):
    t = _transcript(repo / "t.jsonl", 160_000)
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_context_gauge,
        {
            "session_id": "s1",
            "agent_id": "a1",
            "transcript_path": str(t),
            "cwd": str(repo),
        },
    )
    assert out == {}


def test_steward_off_is_silent(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_CONTEXT_STEWARD")
    t = _transcript(repo / "t.jsonl", 160_000)
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_context_gauge,
        {"session_id": "s1", "transcript_path": str(t), "cwd": str(repo)},
    )
    assert out == {}


def test_sub_agent_brief_gets_the_locations(monkeypatch, capsys, repo):
    tool_input = {"description": "fix", "prompt": "Make `charge_card` retry once."}
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_agent,
        {"tool_input": tool_input, "cwd": str(repo)},
    )
    updated = out["updatedInput"]
    assert updated["description"] == "fix"
    assert updated["prompt"].startswith("Make `charge_card` retry once.")
    assert "app/billing.py:10-30  function charge_card" in updated["prompt"]
    # Already enriched: left alone.
    assert cs.agent_brief(repo, updated) is None


def test_sub_agent_brief_without_a_match_is_untouched(monkeypatch, capsys, repo):
    out = _run(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_agent,
        {"tool_input": {"prompt": "Write a haiku."}, "cwd": str(repo)},
    )
    assert out == {}


def test_steward_hooks_install_only_when_asked(monkeypatch):
    from codegraph.cli.commands_init import _claude_hook_specs

    monkeypatch.delenv("CGH_CONTEXT_STEWARD", raising=False)
    markers = {s["marker"] for s in _claude_hook_specs("cgh")}
    assert "cgh-context-gauge" not in markers
    monkeypatch.setenv("CGH_CONTEXT_STEWARD", "1")
    specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
    assert specs["cgh-context-gauge"]["event"] == "PostToolUse"
    assert specs["cgh-precheck-agent"]["matcher"] == "Agent|Task"
