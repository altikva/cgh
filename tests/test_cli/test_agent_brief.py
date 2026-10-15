# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Sub-agent locations (CGH_AGENT_LOCATIONS=1): the brief enriched
#              with cgh's locations, the switch, and the opt-in install.

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


def test_sub_agent_brief_without_a_match_is_untouched(monkeypatch, capsys, repo):
    out = _run(
        monkeypatch,
        capsys,
        {"tool_input": {"prompt": "Write a haiku."}, "cwd": str(repo)},
    )
    assert out == {}


def test_switch_off_is_silent(monkeypatch, capsys, repo):
    monkeypatch.delenv("CGH_AGENT_LOCATIONS")
    tool_input = {"prompt": "Make `charge_card` retry once."}
    out = _run(monkeypatch, capsys, {"tool_input": tool_input, "cwd": str(repo)})
    assert out == {}


def test_hook_installs_only_when_asked(monkeypatch):
    from codegraph.cli.commands_init import _claude_hook_specs

    monkeypatch.delenv("CGH_AGENT_LOCATIONS", raising=False)
    assert "cgh-precheck-agent" not in {s["marker"] for s in _claude_hook_specs("cgh")}
    monkeypatch.setenv("CGH_AGENT_LOCATIONS", "1")
    specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
    assert specs["cgh-precheck-agent"]["matcher"] == "Agent|Task"
