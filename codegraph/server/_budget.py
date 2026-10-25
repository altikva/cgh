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
#              cap_tool_answer applies a per-tool ceiling (TOOL_CAPS) to any
#              tool's answer from the shared tool wrapper, so a tool without
#              its own budget cannot flood the session either. wire_bytes
#              measures an answer as the agent receives it, escaped inside
#              the structured-content envelope.

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from codegraph.analysis.pattern import PATTERN_ANSWER_BYTES

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


def _top_lists(payload: dict[str, Any], keys: list[str]) -> dict[str, list]:
    """The lists ``payload[k]`` for ``k`` in ``keys``, by key."""
    return {k: payload[k] for k in keys if isinstance(payload.get(k), list)}


def _trim(
    payload: dict[str, Any],
    lists_of: Callable[[], dict[str, list]],
    budget: int,
    size: Callable[[dict[str, Any]], int],
    cost: Callable[[Any], int],
) -> int:
    """Pop items from the tail of the longest of the lists ``lists_of()``
    returns until ``size(payload)`` fits ``budget``; returns how many went."""
    dropped = 0
    # The estimate can undershoot (a list emptied to "[]"): measure again.
    for _ in range(4):
        excess = size(payload) - budget
        if excess <= 0:
            break
        lists = lists_of()
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
        lambda: _top_lists(payload, keys),
        max_tokens * CHARS_PER_TOKEN,
        _size,
        lambda item: _item_cost(item, 4),
    )
    if dropped:
        payload["truncated"] = True
        payload["dropped"] = (payload.get("dropped") or 0) + dropped
    return dropped


def dumps_answer(payload: dict[str, Any]) -> str:
    """The compact JSON text a capped tool answers with."""
    return json.dumps(payload, ensure_ascii=False)


def answer_bytes(payload: dict[str, Any]) -> int:
    """Size in bytes of ``dumps_answer(payload)`` once UTF-8 encoded."""
    return len(dumps_answer(payload).encode("utf-8"))


def wire_bytes(text: str) -> int:
    """Bytes an agent receives for the tool answer ``text``. FastMCP sends a
    string result a second time as structured content, ``{"result": text}``,
    and that is the form Claude Code hands the model: every quote and
    backslash of the text arrives escaped, so the answer weighs about a
    tenth more than the string the tool returned."""
    wrapped = json.dumps({"result": text}, ensure_ascii=False, separators=(",", ":"))
    return len(wrapped.encode("utf-8"))


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
        lambda: _top_lists(payload, keys),
        max_bytes - reserve,
        answer_bytes,
        lambda item: len(dumps_answer(item).encode("utf-8")) + 2,
    )
    if dropped:
        payload["truncated"] = True
        payload["dropped"] = (payload.get("dropped") or 0) + dropped
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


# The ceiling and the narrowing advice for each tool's answer. A tool left
# out gets ANSWER_MAX_BYTES and the generic advice. ``keys`` names the lists
# that lose items first; the other lists the answer holds, nested in objects
# included, only go when that is not enough.
@dataclass(frozen=True, slots=True)
class ToolCap:
    max_bytes: int = ANSWER_MAX_BYTES
    hint: str = ""
    keys: tuple[str, ...] = ()


# A summary by nature, or a tool already cut tighter in its own body.
CONTEXT_MAX_BYTES = 6_000
# file_summary is the cheap step before a Read, so it must weigh less than
# one: a token study measured Reads at 3.5 KB on average.
FILE_SUMMARY_MAX_BYTES = 2_048

_GENERIC_HINT = "Narrow the query: a path, a filter or a smaller limit."

TOOL_CAPS: dict[str, ToolCap] = {
    "architecture_overview": ToolCap(
        hint="Lower max_files_per_role, or call domain_map(keyword) for one area."
    ),
    "architecture_report": ToolCap(hint="Lower max_items."),
    "communities": ToolCap(hint="Lower max_communities or raise min_size."),
    "context_for_task": ToolCap(
        max_bytes=CONTEXT_MAX_BYTES,
        hint=(
            "Call again with the same session_id for the next items, lower "
            "max_nodes, or describe the task more narrowly."
        ),
    ),
    "doc_outline": ToolCap(
        hint="search_docs(query) finds one section; file_at_ref reads a line range."
    ),
    "doc_refs": ToolCap(hint="Use a more specific symbol name."),
    "domain_map": ToolCap(hint="Lower limit_per_role or use a more specific keyword."),
    "endpoints": ToolCap(hint="Filter with path_pattern or method."),
    "find_callees": ToolCap(hint="Lower max_depth."),
    "find_callers": ToolCap(
        hint="Use a qualified name (Class.method) or check symbol_lookup first."
    ),
    "file_summary": ToolCap(
        max_bytes=FILE_SUMMARY_MAX_BYTES,
        hint="symbol_lookup(name) for one symbol, or Read a line range.",
    ),
    "find_dead_code": ToolCap(hint="Scope it with file_path (a file or a directory)."),
    "findings": ToolCap(
        hint="Filter with file_path, key_prefix or severity, or lower limit."
    ),
    "fts_search": ToolCap(hint="Lower limit or filter with kind."),
    "hotspots": ToolCap(hint="Lower limit."),
    "impact_of": ToolCap(
        hint=(
            "Lower max_depth, keep one area with focus, or pass max_tokens "
            "for an explicit budget."
        )
    ),
    "impact_report": ToolCap(hint="Pass fewer changed_files per call."),
    "import_cycles": ToolCap(hint="Lower limit."),
    "imports_of": ToolCap(hint="subgraph(file_path, max_tokens=...) sets a budget."),
    "indexed_files": ToolCap(hint="Filter with pattern or path, or lower limit."),
    "knowledge_list": ToolCap(
        hint=(
            "Page with offset=next_offset, lower limit, filter with kind or "
            "tag, or find one entry with knowledge_search(query)."
        )
    ),
    "knowledge_search": ToolCap(hint="Lower limit or filter with kind."),
    "knowledge_terms": ToolCap(hint="Raise min_count."),
    "memory_list": ToolCap(hint="Filter with kind, or use memory_search(query)."),
    "memory_search": ToolCap(hint="Lower limit or filter with kind."),
    "packages": ToolCap(hint="Filter with name, or lower limit."),
    "path_between": ToolCap(hint="Pick closer endpoints."),
    # pattern_search fits its own answer to this ceiling, `more` and
    # `top_dirs` included; this entry only catches what a wrapper adds after.
    "pattern_search": ToolCap(
        max_bytes=PATTERN_ANSWER_BYTES,
        hint="Narrow the pattern, pass a glob, or use count_only=True.",
        keys=("files",),
    ),
    "plan_list": ToolCap(hint="Lower limit or pass agent_only, or use plan_search."),
    "plan_search": ToolCap(hint="Lower limit."),
    "pr_overlap": ToolCap(hint="Lower limit."),
    "rationale": ToolCap(hint="Filter with target or tag, or lower limit."),
    "resume": ToolCap(
        hint="Ask for one part with resume(section=...).",
        keys=(
            "knowledge",
            "recent_summaries",
            "open_plans",
            "digests",
            "known_dead_ends",
        ),
    ),
    "search_docs": ToolCap(hint="Lower limit or use a more specific query."),
    "search_fetched": ToolCap(hint="Lower limit."),
    "shared_types": ToolCap(hint="Filter with name, or lower limit."),
    "subgraph": ToolCap(hint="Lower depth, or pass max_tokens for an explicit budget."),
    "symbol_lookup": ToolCap(hint="Filter with role or layer."),
    "tests_for": ToolCap(hint="Ask for a symbol instead of a whole file."),
    "untested": ToolCap(hint="Filter with role or layer."),
    "visualize_graph": ToolCap(
        hint="Lower max_nodes, or pass file_path or symbol_name."
    ),
    "who_knows": ToolCap(hint="Ask for one file."),
}

# Arguments through which a caller sets its own size budget. When one is
# passed, the tool honours it and the ceiling stays out of the way.
SIZE_ARGS = ("max_tokens", "budget_kb")


def _nested_lists(
    payload: dict[str, Any], keys: tuple[str, ...], depth: int = 3
) -> dict[str, list]:
    """The non-empty lists the answer holds, by dotted path: ``keys`` at
    the top level when given, else every list reached through objects (not
    through list items, which go whole)."""
    if keys:
        return {k: v for k, v in _top_lists(payload, list(keys)).items() if v}
    found: dict[str, list] = {}

    def walk(node: dict[str, Any], prefix: str, level: int) -> None:
        for k, v in node.items():
            path = f"{prefix}{k}"
            if isinstance(v, list) and v:
                found[path] = v
            elif isinstance(v, dict) and level < depth:
                walk(v, f"{path}.", level + 1)

    walk(payload, "", 1)
    return found


def _longest_string(node: Any) -> tuple[Any, Any, int]:
    """(container, key, length) of the longest string in ``node``."""
    best: tuple[Any, Any, int] = (None, None, 0)
    stack = [node]
    while stack:
        cur = stack.pop()
        items = cur.items() if isinstance(cur, dict) else enumerate(cur)
        for k, v in items:
            if isinstance(v, str) and len(v) > best[2]:
                best = (cur, k, len(v))
            elif isinstance(v, dict | list):
                stack.append(v)
    return best


_CUT_MARK = "\n[cut]"


def clip_strings(payload: dict[str, Any], budget: int) -> int:
    """Shorten the longest strings, at a line break when one is near, until
    the answer fits ``budget``; returns how many strings were cut."""
    cut = 0
    for _ in range(8):
        excess = answer_bytes(payload) - budget
        if excess <= 0:
            break
        container, key, length = _longest_string(payload)
        if container is None or length <= 200:
            break
        text: str = container[key]
        # Characters, not bytes: overshoot a little so one pass usually does.
        keep = max(160, length - excess - len(_CUT_MARK) - 16)
        head = text[:keep]
        newline = head.rfind("\n")
        if newline > keep // 2:
            head = head[:newline]
        container[key] = head + _CUT_MARK
        cut += 1
    return cut


def cap_tool_answer(tool: str, result: Any, kwargs: dict[str, Any]) -> Any:
    """Bring a tool's answer under its ceiling in TOOL_CAPS.

    An answer that fits comes back unchanged. A larger JSON object comes
    back as compact JSON with whole list items dropped (the longest list
    first, from its tail), then the longest strings shortened if that is not
    enough, and ``truncated``, ``dropped`` and ``hint`` set. Plain text is
    cut at a line break with a closing note. Anything else, and every answer
    of a call that passed one of SIZE_ARGS, is left alone.
    """
    if not isinstance(result, str):
        return result
    if any(kwargs.get(arg) for arg in SIZE_ARGS):
        return result
    spec = TOOL_CAPS.get(tool, ToolCap())
    if len(result.encode("utf-8")) <= spec.max_bytes:
        return result
    hint = (
        f"Answer cut to about {spec.max_bytes // 1000} KB. {spec.hint or _GENERIC_HINT}"
    )
    try:
        payload = json.loads(result)
    except ValueError:
        return _cut_text(result, spec.max_bytes, hint)
    if not isinstance(payload, dict):
        return result
    if answer_bytes(payload) <= spec.max_bytes:
        return dumps_answer(payload)
    budget = spec.max_bytes - len(hint.encode("utf-8")) - 48
    dropped = 0
    # The lists the spec names go first; the others only if that is not
    # enough, so the ceiling holds whatever the answer's shape.
    for keys in (spec.keys, ()) if spec.keys else ((),):
        dropped += _trim(
            payload,
            lambda keys=keys: _nested_lists(payload, keys),
            budget,
            answer_bytes,
            lambda item: len(dumps_answer(item).encode("utf-8")) + 2,
        )
    clipped = clip_strings(payload, budget)
    if dropped or clipped:
        payload["truncated"] = True
        payload["dropped"] = (payload.get("dropped") or 0) + dropped
        payload["hint"] = hint
    return dumps_answer(payload)


def _cut_text(text: str, max_bytes: int, hint: str) -> str:
    """Plain text cut at a line break under ``max_bytes``, with a note."""
    note = f"\n[{hint}]"
    room = max_bytes - len(note.encode("utf-8"))
    head = text.encode("utf-8")[: max(room, 0)].decode("utf-8", "ignore")
    newline = head.rfind("\n")
    if newline > len(head) // 2:
        head = head[:newline]
    return head + note
