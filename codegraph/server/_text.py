# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The grep-like text answer of pattern_search, find_callers,
#              search_symbols and symbol_lookup, opted into with format="text"
#              or the [mcp] answer_format setting. Measured on cgh's own index
#              it weighs 6 to 53% fewer tokens than compact JSON: keys are not
#              repeated on every row and far less text arrives escaped.
#
#              Layout: one header line of key=value metadata, then the rows
#              grouped under their file path, one `[scope]` section per scope
#              of a federated answer. A value is written bare when it is a
#              plain word or path, as compact JSON otherwise; a hit text or a
#              doc holding a newline, a tab or a leading quote is JSON-quoted.
#              Null and empty values are left out.
#
#              The text is rendered from the answer the JSON fit already
#              produced, then measured with wire_bytes: when it is still over
#              the tool's ceiling, rows go from the tail and it is rendered
#              again, so the ceiling holds on the text the agent receives.

from __future__ import annotations

import json
import re
from typing import Any

from codegraph.core.config import ANSWER_FORMATS
from codegraph.server._budget import TOOL_CAPS, ToolCap, fit_wire, wire_bytes

# The tools that answer text on request, and the list holding their rows.
TEXT_TOOLS: dict[str, str] = {
    "pattern_search": "files",
    "find_callers": "callers",
    "search_symbols": "results",
    "symbol_lookup": "definitions",
}

# pattern_search's sentence answers add these lists next to the hits.
_SECONDARY = ("symbols", "docs")

# Per row list: the key of its location and the keys written by position
# after it, before the remaining key=value pairs.
_LOC_KEY = {
    "callers": "line",
    "results": "line",
    "definitions": "lines",
    "symbols": "lines",
    "docs": "lines",
}
_POSITIONAL = {
    "callers": ("caller", "resolution"),
    "results": ("kind", "name"),
    "definitions": ("kind", "name"),
    "symbols": ("kind", "name"),
    "docs": ("kind", "name"),
}

_BARE = re.compile(r"[\w./*:@+-]+")
_BARE_GLOB = re.compile(r"[A-Za-z0-9_./*@+-]+")
_LOC = re.compile(r"\d+(?:-\d+)?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _is_json_literal(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def tok(value: Any) -> str:
    """A value token: bare for a plain word or path, compact JSON else (a
    string that would read as a number or a JSON literal is quoted)."""
    if (
        isinstance(value, str)
        and _BARE.fullmatch(value)
        and not _is_json_literal(value)
    ):
        return value
    return _json(value)


def text_cell(text: str) -> str:
    """A hit text or a doc as written, unless a newline, a tab, another
    control character, a leading quote or a leading blank would make it
    ambiguous: then JSON-quoted, so a leading quote always means JSON."""
    if _CONTROL.search(text) or text.startswith('"') or text[:1].isspace():
        return _json(text)
    return text


def path_tok(path: Any) -> str:
    """A file path as a line of its own (or before `:line`): bare unless it
    could be read as a section line or holds a blank."""
    if (
        not isinstance(path, str)
        or not path
        or path[0] in '"[-'
        or path.endswith(":")
        or " " in path
        or _CONTROL.search(path)
    ):
        return _json(path)
    return path


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _kv(row: dict[str, Any]) -> str:
    return " ".join(f"{k}={tok(v)}" for k, v in row.items() if _present(v))


def _top_dirs(dirs: Any) -> str:
    """`glob:hits,...` when every entry is a plain glob and a count, else
    compact JSON."""
    if isinstance(dirs, list) and all(
        isinstance(d, dict)
        and set(d) == {"glob", "hits"}
        and isinstance(d["glob"], str)
        and _BARE_GLOB.fullmatch(d["glob"])
        and isinstance(d["hits"], int)
        for d in dirs
    ):
        return ",".join(f"{d['glob']}:{d['hits']}" for d in dirs)
    return _json(dirs)


def _header(meta: dict[str, Any]) -> str:
    parts = []
    for key, value in meta.items():
        if not _present(value):
            continue
        shown = _top_dirs(value) if key == "top_dirs" else tok(value)
        parts.append(f"{key}={shown}")
    return " ".join(parts)


def _loc(row: dict[str, Any], key: str) -> str | None:
    """The row's location token, popped from ``row``, when it has the
    expected form: an int line or a `start-end` range."""
    value = row.get(key)
    if key == "line" and isinstance(value, int) and not isinstance(value, bool):
        row.pop(key)
        return str(value)
    if key == "lines" and isinstance(value, str) and _LOC.fullmatch(value):
        row.pop(key)
        return value
    return None


def _positional(row: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """Tokens of ``keys`` in order, popped from ``row``, up to the first
    one the row lacks: the rest stay key=value."""
    out: list[str] = []
    for key in keys:
        if not _present(row.get(key)):
            break
        out.append(tok(row.pop(key)))
    return out


def _line(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _grouped_rows(rows: list[dict[str, Any]], name: str) -> list[str]:
    """Rows under their file path: `path`, then `  loc pos... key=value`."""
    out: list[str] = []
    current: object = object()
    for original in rows:
        row = dict(original)
        file = row.pop("file", None)
        loc = _loc(row, _LOC_KEY[name])
        pos = _positional(row, _POSITIONAL[name])
        body = _line(loc or "", *pos, _kv(row))
        if not isinstance(file, str):
            current = object()
            out.append(_line("-", body))
            continue
        if file != current:
            out.append(path_tok(file))
            current = file
        out.append("  " + body)
    return out


def _definition_rows(rows: list[dict[str, Any]]) -> list[str]:
    """symbol_lookup: `path:start-end kind name key=value`, then the doc on
    an indented line of its own."""
    out: list[str] = []
    for original in rows:
        row = dict(original)
        file = row.pop("file", None)
        doc = row.pop("doc", None)
        loc = _loc(row, "lines") or ""
        pos = _positional(row, _POSITIONAL["definitions"])
        head = f"{path_tok(file)}:{loc}" if isinstance(file, str) else "-"
        out.append(_line(head, *pos, _kv(row)))
        if isinstance(doc, str) and doc:
            out.append("  " + text_cell(doc))
        elif _present(doc):
            out.append(f"  doc={tok(doc)}")
    return out


def _hit_groups(groups: list[dict[str, Any]], count_only: bool) -> list[str]:
    """pattern_search: `path`, then `  line: text` per hit, like ripgrep;
    `path:count` for a count_only answer."""
    out: list[str] = []
    for original in groups:
        group = dict(original)
        file = group.pop("file", None)
        hits = group.pop("hits", None)
        count = group.pop("count", None) if count_only else None
        extra = _kv(group)
        if count is not None:
            out.append(_line(f"{path_tok(file)}:{tok(count)}", extra))
            continue
        out.append(_line(path_tok(file), extra))
        for hit in hits or []:
            if isinstance(hit, list) and len(hit) == 2:
                line = hit[0] if isinstance(hit[0], int) else _json(hit[0])
                text = hit[1] if isinstance(hit[1], str) else _json(hit[1])
                out.append(f"  {line}: {text_cell(text)}")
            else:
                out.append(f"  {_json(hit)}")
    return out


def _scoped(rows: list[dict[str, Any]], render) -> list[str]:
    """``render(rows)``, one `[scope]` section per scope when the rows carry
    one; rows without a scope come first, with no heading."""
    if not any(isinstance(r, dict) and "scope" in r for r in rows):
        return render(rows)
    sections: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        sections.setdefault(row.get("scope"), []).append(
            {k: v for k, v in row.items() if k != "scope"}
        )
    out: list[str] = []
    if None in sections:
        out += render(sections.pop(None))
    for scope, part in sections.items():
        out.append(f"[{scope}]")
        out += render(part)
    return out


def _is_rows(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(r, dict) for r in value)


def render_text(tool: str, payload: dict[str, Any]) -> str:
    """The text answer of ``tool`` for its JSON ``payload``."""
    primary = TEXT_TOOLS[tool]
    lists = [primary, *(_SECONDARY if tool == "pattern_search" else ())]
    more = payload.get("more") if tool == "pattern_search" else None
    held = {k for k in lists if _is_rows(payload.get(k))}
    if isinstance(more, dict):
        held.add("more")
    meta = {k: v for k, v in payload.items() if k not in held}
    out = [_header(meta)]
    if primary in held:
        rows = payload[primary]
        if tool == "pattern_search":
            count_only = bool(payload.get("count_only"))
            out += _scoped(rows, lambda r: _hit_groups(r, count_only))
        elif tool == "symbol_lookup":
            out += _scoped(rows, _definition_rows)
        else:
            out += _scoped(rows, lambda r: _grouped_rows(r, primary))
    for name in lists[1:]:
        if name in held and payload[name]:
            out.append(f"{name}:")
            out += _grouped_rows(payload[name], name)
    if isinstance(more, dict) and more:
        total, shown = payload.get("total"), payload.get("shown")
        left = (
            f" {total - shown} more hit(s) not shown"
            if isinstance(total, int) and isinstance(shown, int)
            else " more hits not shown"
        )
        out.append(f"[cut]{left}, per file:")
        out += [f"  {path_tok(f)} +{tok(n)}" for f, n in more.items()]
    return "\n".join(out)


def _drop_one(tool: str, payload: dict[str, Any]) -> bool:
    """Drop the last row of the answer: a secondary list first, then the
    primary one (for pattern_search the last hit of the last file)."""
    primary = TEXT_TOOLS[tool]
    for name in (*(_SECONDARY if tool == "pattern_search" else ()), primary):
        rows = payload.get(name)
        if not _is_rows(rows) or not rows:
            continue
        if name == "files":
            last = rows[-1]
            hits = last.get("hits")
            if isinstance(hits, list) and len(hits) > 1:
                hits.pop()
            else:
                rows.pop()
            if isinstance(payload.get("shown"), int):
                payload["shown"] -= 1
        else:
            rows.pop()
        return True
    return False


def text_answer(tool: str, result: Any, max_bytes: int | None = None) -> Any:
    """``result``, a JSON-object answer of ``tool``, as text at most
    ``max_bytes`` (the tool's ceiling) as received. Anything else (plain
    text, a JSON list) is returned unchanged."""
    if tool not in TEXT_TOOLS or not isinstance(result, str):
        return result
    if not result.startswith("{"):
        return result
    try:
        payload = json.loads(result)
    except ValueError:
        return result
    if not isinstance(payload, dict):
        return result
    spec = TOOL_CAPS.get(tool, ToolCap())
    cap = max_bytes if max_bytes is not None else spec.max_bytes
    text = render_text(tool, payload)
    if wire_bytes(text) <= cap:
        return text
    hint = f"Answer cut to about {cap // 1000} KB. {spec.hint}".strip()
    payload["truncated"] = True
    payload.setdefault("hint", hint)
    already = payload.get("dropped")
    base = already if isinstance(already, int) else 0
    dropped = 0
    for _ in range(256):
        excess = wire_bytes(text) - cap
        if excess <= 0 or not _has_rows(tool, payload):
            break
        # Drop about as many rows as the excess weighs, at the average
        # weight of a row, then measure again.
        rows = _row_count(tool, payload)
        body = wire_bytes(text) - wire_bytes(text.split("\n", 1)[0])
        per_row = max(1, body // max(1, rows))
        for _ in range(max(1, excess // per_row)):
            if not _drop_one(tool, payload):
                break
            dropped += 1
        payload["dropped"] = base + dropped
        text = render_text(tool, payload)
    return fit_wire(text, cap, hint)


def _row_count(tool: str, payload: dict[str, Any]) -> int:
    """Rows the text shows: one per hit for pattern_search, else per item."""
    names = (TEXT_TOOLS[tool], *(_SECONDARY if tool == "pattern_search" else ()))
    count = 0
    for name in names:
        rows = payload.get(name)
        if not _is_rows(rows):
            continue
        for row in rows:
            hits = row.get("hits") if name == "files" else None
            count += len(hits) if isinstance(hits, list) and hits else 1
    return count


def _has_rows(tool: str, payload: dict[str, Any]) -> bool:
    names = (TEXT_TOOLS[tool], *(_SECONDARY if tool == "pattern_search" else ()))
    return any(_is_rows(payload.get(n)) and payload.get(n) for n in names)


def resolve_format(requested: Any, root: Any) -> str:
    """The answer format of a call: ``requested`` when it names one, else
    the [mcp] answer_format setting (CGH_ANSWER_FORMAT wins), else json."""
    if isinstance(requested, str) and requested.strip().lower() in ANSWER_FORMATS:
        return requested.strip().lower()
    from codegraph.core.config import load_config

    return load_config(root).answer_format
