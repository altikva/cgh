# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Code locations for a sub-agent's brief (CGH_AGENT_LOCATIONS=1).
#              enabled      whether the switch is on
#              agent_brief  the sub-agent's tool input with the locations cgh
#                           finds for its prompt appended, so it starts from
#                           file:line instead of exploring the tree; None when
#                           there is nothing to add

from __future__ import annotations

import os
from pathlib import Path

_MAX_LOCATIONS = 12
_MARK = "[cgh] Start from"


def enabled() -> bool:
    return os.environ.get("CGH_AGENT_LOCATIONS") == "1"


def agent_brief(root: Path, tool_input: dict) -> dict | None:
    """The sub-agent's tool input with cgh's locations appended to its
    prompt, or None when there is nothing to add."""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or _MARK in prompt:
        return None
    from codegraph.analysis.locations import task_locations

    try:
        locs = task_locations(root, prompt, max_lines=_MAX_LOCATIONS)
    except Exception:
        return None  # best effort: the sub-agent starts without them
    if not locs:
        return None
    block = (
        f"\n\n{_MARK} these locations, found in the code graph for this "
        "brief (Read with offset/limit around each; use cgh for the rest instead "
        "of exploring the tree):\n" + "\n".join(f"  {loc}" for loc in locs)
    )
    return {**tool_input, "prompt": prompt + block}
