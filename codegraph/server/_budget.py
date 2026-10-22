# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Size budgets for a tool's JSON answer. fit_tokens trims the
#              listed result arrays from their tail (the farthest or least
#              relevant items, the arrays being ordered nearest first) until
#              the indented JSON fits about 4 characters per token, then
#              marks the payload truncated with the number of items dropped.
#              Nothing changes without a budget. cap_answer does the same
#              against a byte ceiling on the compact JSON every capped tool
#              answers with, so no answer grows past ANSWER_MAX_BYTES, and
#              adds a hint on how to narrow the query. The JSON stays valid:
#              whole list items go, never a cut in the middle of the text.

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

# Rough characters per token of JSON output.
CHARS_PER_TOKEN = 4

# Ceiling for a capped tool answer. Every byte of an answer is re-read on each
# later turn of the session, so a single 40 KB answer costs far more than its
# own size over a long session.
ANSWER_MAX_BYTES = 10_000


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, indent=2))


def _item_cost(item: Any, indent: int) -> int:
    """Characters one array item adds to the indented payload: its own
    indented text, the nesting indent on each of its lines, a comma and a
    newline."""
    text = json.dumps(item, indent=2)
    return len(text) + indent * (text.count("\n") + 1) + 2


def _trim(
    payload: dict[str, Any],
    keys: list[str],
    budget: int,
    size: Callable[[dict[str, Any]], int],
    cost: Callable[[Any], int],
) -> int:
    """Pop items from the tail of the longest of ``payload[k]`` until
    ``size(payload)`` fits ``budget``; returns how many went."""
    dropped = 0
    # The estimate can undershoot (a list emptied to "[]"): measure again.
    for _ in range(4):
        excess = size(payload) - budget
        if excess <= 0:
            break
        lists = {k: payload[k] for k in keys if isinstance(payload.get(k), list)}
        while excess > 0 and any(lists.values()):
            key = max(lists, key=lambda k: len(lists[k]))
            excess -= cost(lists[key].pop())
            dropped += 1
        if not any(lists.values()):
            break
    return dropped


def fit_tokens(payload: dict[str, Any], keys: list[str], max_tokens: int) -> int:
    """Trim ``payload[k]`` for ``k`` in ``keys`` until the payload fits
    ``max_tokens`` (approximate). Items go from the end of the longest
    array first. Sets ``truncated`` and ``dropped`` when anything went;
    returns the number of items dropped. ``max_tokens`` <= 0 is no budget."""
    if max_tokens <= 0:
        return 0
    dropped = _trim(
        payload,
        keys,
        max_tokens * CHARS_PER_TOKEN,
        _size,
        lambda item: _item_cost(item, 4),
    )
    if dropped:
        payload["truncated"] = True
        payload["dropped"] = payload.get("dropped", 0) + dropped
    return dropped


def dumps_answer(payload: dict[str, Any]) -> str:
    """The compact JSON text a capped tool answers with."""
    return json.dumps(payload, ensure_ascii=False)


def answer_bytes(payload: dict[str, Any]) -> int:
    """Size in bytes of ``dumps_answer(payload)`` once UTF-8 encoded."""
    return len(dumps_answer(payload).encode("utf-8"))


def cap_answer(
    payload: dict[str, Any],
    keys: list[str],
    hint: str,
    max_bytes: int = ANSWER_MAX_BYTES,
) -> int:
    """Trim ``payload[k]`` for ``k`` in ``keys`` until the compact JSON is at
    most ``max_bytes``. Items go from the end of the longest list first. When
    anything went, sets ``truncated``, adds to ``dropped`` and sets ``hint``;
    returns the number of items dropped."""
    # The hint is part of the answer when anything is cut, so count it in.
    reserve = len(hint.encode("utf-8")) + 48
    dropped = _trim(
        payload,
        keys,
        max_bytes - reserve,
        answer_bytes,
        lambda item: len(dumps_answer(item).encode("utf-8")) + 2,
    )
    if dropped:
        payload["truncated"] = True
        payload["dropped"] = payload.get("dropped", 0) + dropped
        payload["hint"] = hint
    return dropped


def drop_parent_scope(rows: list[dict[str, Any]]) -> bool:
    """Remove ``scope`` from every row when they all come from the parent:
    in a repo with no federated children the tag says nothing. Returns True
    when the rows were left tagged (more than one scope)."""
    if any(r.get("scope", "parent") != "parent" for r in rows):
        return True
    for r in rows:
        r.pop("scope", None)
    return False
