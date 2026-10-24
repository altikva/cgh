# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: What cgh appends to a sub-agent's brief.
#              enabled      whether the locations switch (CGH_AGENT_LOCATIONS=1)
#                           is on
#              rule_enabled whether the one-line cgh rule is on (default on;
#                           [hooks] agent_rule = false or CGH_HOOK_AGENT_RULE=0
#                           turns it off)
#              rule_line    the rule, naming the session's MCP server prefix
#                           when the repo's .mcp.json tells it
#              agent_brief  the sub-agent's tool input with the locations cgh
#                           finds for its prompt and the rule appended; None
#                           when there is nothing to add

from __future__ import annotations

import json
import os
from pathlib import Path

_MAX_LOCATIONS = 12
_MARK = "[cgh] Start from"
_RULE_MARK = "[cgh] Locate code with"
# The rule line must stay short: it rides on every sub-agent brief.
RULE_MAX_BYTES = 300


def enabled() -> bool:
    return os.environ.get("CGH_AGENT_LOCATIONS") == "1"


def rule_enabled(root: Path | None) -> bool:
    from codegraph.cli.hook_answers import hooks_setting

    return hooks_setting("agent_rule", "CGH_HOOK_AGENT_RULE", True, root)


def _tool_prefix(root: Path) -> str | None:
    """The MCP tool prefix the repo's .mcp.json gives cgh: mcp__cgh__ or
    the legacy mcp__codegraph__, or None when the file does not say (a
    plugin or user-scope install names its tools differently)."""
    from codegraph.integrations.mcp_name import LEGACY_SERVER_KEY, SERVER_KEY

    try:
        data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        servers = data.get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(servers, dict):
        return None
    if SERVER_KEY in servers:
        return f"mcp__{SERVER_KEY}__"
    if LEGACY_SERVER_KEY in servers:
        return f"mcp__{LEGACY_SERVER_KEY}__"
    return None


def rule_line(root: Path) -> str:
    """One line telling the sub-agent to locate with cgh before reading."""
    prefix = _tool_prefix(root)
    tools = f"the {prefix}* MCP tools" if prefix else "the cgh MCP tools"
    return (
        f"{_RULE_MARK} {tools} (symbol_lookup, pattern_search, find_callers) "
        "before Read or Grep, then Read only the line ranges they return."
    )


def _locations_block(root: Path, prompt: str) -> str:
    if not enabled() or _MARK in prompt:
        return ""
    from codegraph.analysis.locations import task_locations

    try:
        locs = task_locations(root, prompt, max_lines=_MAX_LOCATIONS)
    except Exception:
        return ""  # best effort: the sub-agent starts without them
    if not locs:
        return ""
    return (
        f"\n\n{_MARK} these locations, found in the code graph for this "
        "brief (Read with offset/limit around each; use cgh for the rest instead "
        "of exploring the tree):\n" + "\n".join(f"  {loc}" for loc in locs)
    )


def agent_brief(root: Path, tool_input: dict) -> dict | None:
    """The sub-agent's tool input with cgh's locations (when switched on)
    and the cgh rule (unless opted out) appended to its prompt, or None
    when there is nothing to add."""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    addition = _locations_block(root, prompt)
    if _RULE_MARK not in prompt and rule_enabled(root):
        addition += "\n\n" + rule_line(root)
    if not addition:
        return None
    return {**tool_input, "prompt": prompt + addition}
