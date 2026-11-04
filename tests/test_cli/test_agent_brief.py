# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The sub-agent brief: the cgh rule line (default on, opt-out),
#              the locations (CGH_AGENT_LOCATIONS=1), and the default install.

from __future__ import annotations

import argparse
import io
import json

import pytest

from codegraph.cli import agent_brief as ab
from codegraph.cli import commands_hooks as h
from codegraph.core.fts import get_fts_conn, upsert_symbol


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with an FTS index holding one function, the switch on."""
    conn = get_fts_conn(tmp_path)
    src = tmp_path / "app" / "billing.py"
    src.parent.mkdir()
    src.write_text("x = 1\n")
    upsert_symbol(conn, "b::charge_card", "function", "charge_card", str(src), 10, 30)
    conn.commit()
    conn.close()
    monkeypatch.setenv("CGH_AGENT_LOCATIONS", "1")
    monkeypatch.delenv("CGH_HOOK_AGENT_RULE", raising=False)
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home")
    return tmp_path


def _run(monkeypatch, capsys, payload: dict) -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_agent(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else {}


def test_sub_agent_brief_gets_the_locations(monkeypatch, capsys, repo):
    tool_input = {"description": "fix", "prompt": "Make `charge_card` retry once."}
    out = _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)})
    updated = out["updatedInput"]
    assert updated["description"] == "fix"
    assert updated["prompt"].startswith("Make `charge_card` retry once.")
    assert "app/billing.py:10-30  function charge_card" in updated["prompt"]
    # Already enriched: left alone.
    assert ab.agent_brief(repo, updated) is None


def test_sub_agent_brief_without_a_match_gets_only_the_rule(monkeypatch, capsys, repo):
    out = _run(
        monkeypatch,
        capsys,
        {"tool_input": {"prompt": "Write a haiku."}, "cwd": str(repo)},
    )
    assert out["updatedInput"]["prompt"] == "Write a haiku.\n\n" + ab.rule_line(repo)


def test_locations_switch_off_still_appends_the_rule(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_AGENT_LOCATIONS")
    tool_input = {"prompt": "Make `charge_card` retry once."}
    out = _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)})
    prompt = out["updatedInput"]["prompt"]
    assert "[cgh] Start from" not in prompt
    assert "app/billing.py" not in prompt
    assert prompt.endswith(ab.rule_line(repo))


def test_rule_is_appended_once(monkeypatch, capsys, repo):
    tool_input = {"prompt": "Make `charge_card` retry once."}
    out = _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)})
    updated = out["updatedInput"]
    assert updated["prompt"].count(ab.rule_line(repo)) == 1
    assert ab.agent_brief(repo, updated) is None
    again = _run(monkeypatch, capsys, {"tool_input": updated, "cwd": str(repo)})
    assert again == {}


def test_rule_line_is_short_and_names_the_prefix(repo):
    assert "the cgh MCP tools" in ab.rule_line(repo)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"codegraph": {}}}))
    assert "mcp__codegraph__*" in ab.rule_line(repo)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"cgh": {}}}))
    line = ab.rule_line(repo)
    assert "mcp__cgh__*" in line
    for name in ("symbol_lookup", "pattern_search", "find_callers"):
        assert name in line
    assert "\n" not in line
    assert len(("\n\n" + line).encode()) < ab.RULE_MAX_BYTES
    (repo / ".mcp.json").write_text("not json")
    assert "the cgh MCP tools" in ab.rule_line(repo)


def test_rule_opt_out_by_env(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_AGENT_LOCATIONS")
    monkeypatch.setenv("CGH_HOOK_AGENT_RULE", "0")
    tool_input = {"prompt": "Make `charge_card` retry once."}
    assert _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)}) == {}


def test_rule_opt_out_by_config(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_AGENT_LOCATIONS")
    (repo / ".codegraph" / "config.toml").write_text("[hooks]\nagent_rule = false\n")
    tool_input = {"prompt": "Make `charge_card` retry once."}
    assert _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)}) == {}


def test_opt_out_keeps_the_locations(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_HOOK_AGENT_RULE", "0")
    tool_input = {"prompt": "Make `charge_card` retry once."}
    out = _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)})
    prompt = out["updatedInput"]["prompt"]
    assert "app/billing.py:10-30" in prompt
    assert ab.rule_line(repo) not in prompt


def test_outside_a_cgh_repo_is_silent(monkeypatch, capsys, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    tool_input = {"prompt": "Make `charge_card` retry once."}
    assert _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(bare)}) == {}


def test_bad_payload_never_blocks(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_agent(argparse.Namespace())
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_hook_installs_by_default(monkeypatch, tmp_path):
    from pathlib import Path

    from codegraph.cli.commands_init import (
        _CLAUDE_HOOK_MARKERS,
        _claude_hook_specs,
        ensure_claude_hooks_installed,
    )

    monkeypatch.delenv("CGH_AGENT_LOCATIONS", raising=False)
    specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
    assert specs["cgh-precheck-agent"]["matcher"] == "Agent|Task"
    assert "_hook_precheck_agent" in specs["cgh-precheck-agent"]["command"]
    assert any(m[0] == "cgh-precheck-agent" for m in _CLAUDE_HOOK_MARKERS)
    ensure_claude_hooks_installed(tmp_path)
    ensure_claude_hooks_installed(tmp_path)  # idempotent
    local = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    agent = [g for g in local["hooks"]["PreToolUse"] if g["matcher"] == "Agent|Task"]
    assert len(agent) == 1
    plugin = json.loads(
        (Path(__file__).parents[2] / ".claude-plugin" / "hooks.json").read_text()
    )
    commands = [
        hook["command"]
        for group in plugin["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert "cgh _hook_precheck_agent" in commands
