# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Approximate token budget for a tool's JSON answer. fit_tokens
#              trims the listed result arrays from their tail (the farthest
#              or least relevant items, the arrays being ordered nearest
#              first) until the indented JSON fits about 4 characters per
#              token, then marks the payload truncated with the number of
#              items dropped. Nothing changes without a budget.

from __future__ import annotations

import json
from typing import Any

# Rough characters per token of JSON output.
CHARS_PER_TOKEN = 4


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, indent=2))


def _item_cost(item: Any, indent: int) -> int:
    """Characters one array item adds to the indented payload: its own
    indented text, the nesting indent on each of its lines, a comma and a
    newline."""
    text = json.dumps(item, indent=2)
    return len(text) + indent * (text.count("\n") + 1) + 2


def fit_tokens(payload: dict[str, Any], keys: list[str], max_tokens: int) -> int:
    """Trim ``payload[k]`` for ``k`` in ``keys`` until the payload fits
    ``max_tokens`` (approximate). Items go from the end of the longest
    array first. Sets ``truncated`` and ``dropped`` when anything went;
    returns the number of items dropped. ``max_tokens`` <= 0 is no budget."""
    if max_tokens <= 0:
        return 0
    budget = max_tokens * CHARS_PER_TOKEN
    dropped = 0
    # The estimate can undershoot (a list emptied to "[]"): measure again.
    for _ in range(4):
        excess = _size(payload) - budget
        if excess <= 0:
            break
        lists = {k: payload[k] for k in keys if isinstance(payload.get(k), list)}
        while excess > 0 and any(lists.values()):
            key = max(lists, key=lambda k: len(lists[k]))
            excess -= _item_cost(lists[key].pop(), 4)
            dropped += 1
        if not any(lists.values()):
            break
    if dropped:
        payload["truncated"] = True
        payload["dropped"] = payload.get("dropped", 0) + dropped
    return dropped
