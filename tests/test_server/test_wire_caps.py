# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Every tool's answer ceiling holds on the size the agent
#              receives (wire_bytes: the answer escaped inside the
#              structured-content envelope), not on the raw string. Covers
#              the shared wrapper for each registered tool, with answers
#              heavy in quotes, backslashes and line breaks, non-ASCII text,
#              JSON lists, plain text and answers made of many short strings,
#              and the tool-specific fits of file_summary, knowledge_search
#              and knowledge_list.

from __future__ import annotations

import asyncio
import json

import pytest

import codegraph.server as _srv
from codegraph.server._budget import (
    ANSWER_MAX_BYTES,
    TOOL_CAPS,
    ToolCap,
    answer_bytes,
    cap_answer,
    cap_tool_answer,
    dumps_answer,
    wire_bytes,
)
from codegraph.server.tools_insight import _fit_summary
from codegraph.server.tools_knowledge import (
    LIST_TARGET_BYTES,
    SEARCH_TARGET_BYTES,
    _fit_list_answer,
    _fit_search_answer,
)

# Each quote, backslash and line break of the answer weighs double, or more,
# once escaped in the envelope.
_ESCAPY = 'raise ValueError("bad \\"path\\" C:\\\\temp\\\\x")\n    return "ok"\n'
_ACCENTS = "Prénom, numéro, réessayez « plus tard », 注文の合計金額 "


def _registered_tools() -> list[str]:
    return sorted(t.name for t in asyncio.run(_srv.mcp.list_tools()))


_TOOLS = _registered_tools()


def _cap(tool: str) -> int:
    return TOOL_CAPS.get(tool, ToolCap()).max_bytes


def _escapy_payload(tool: str) -> dict:
    payload: dict = {
        "query": _ESCAPY,
        "results": [
            {"file": f"src/mod_{i}.py", "line": i, "text": _ESCAPY * 2}
            for i in range(300)
        ],
    }
    for key in TOOL_CAPS.get(tool, ToolCap()).keys:
        payload[key] = [{"id": i, "body": _ESCAPY * 3} for i in range(100)]
    return payload


def test_every_capped_tool_is_registered():
    assert set(TOOL_CAPS) <= set(_TOOLS)


def test_wire_size_counts_the_escapes():
    text = dumps_answer({"code": _ESCAPY * 50})
    assert wire_bytes(text) > len(text.encode()) * 1.1
    assert answer_bytes({"code": _ESCAPY * 50}) == wire_bytes(text)


@pytest.mark.parametrize("tool", _TOOLS)
def test_wrapper_holds_each_tools_cap_on_the_wire(monkeypatch, tool):
    """Through the shared wrapper, the last step of every tool call."""
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)
    monkeypatch.setattr(_srv, "_root", None)
    raw = json.dumps(_escapy_payload(tool), indent=2)

    def fake() -> str:
        return raw

    fake.__name__ = tool
    out = _srv._logged_tool(fake)()
    assert wire_bytes(out) <= _cap(tool)
    assert json.loads(out)["truncated"] is True


@pytest.mark.parametrize("tool", _TOOLS)
def test_cap_tool_answer_holds_on_the_wire_for_plain_text(tool):
    text = "\n".join(f'row {i}: "{_ESCAPY}"' for i in range(2000))
    out = cap_tool_answer(tool, text, {})
    assert wire_bytes(out) <= _cap(tool)
    assert out.splitlines()[-1].startswith("[Answer cut to about")


def test_non_ascii_heavy_answer_fits():
    payload = {
        "hits": [{"title": _ACCENTS, "body": _ACCENTS * 20} for i in range(200)],
        "note": _ACCENTS * 50,
    }
    out = cap_tool_answer("knowledge_search", json.dumps(payload), {})
    assert wire_bytes(out) <= ANSWER_MAX_BYTES
    # Kept as is, not escaped to \uXXXX.
    assert "\\u00e9" not in out and "é" in out
    assert json.loads(out)["hits"][0]["title"] == _ACCENTS


def test_many_short_strings_still_fit():
    """No list to trim and no string long enough to clip: the heaviest keys
    go whole, the first named under `omitted`."""
    payload = {f"key_{i}": f'"value" {i} \\ é' * 8 for i in range(2000)}
    out = cap_tool_answer("graph_stats", json.dumps(payload), {})
    assert wire_bytes(out) <= ANSWER_MAX_BYTES
    parsed = json.loads(out)
    assert parsed["truncated"] is True and parsed["omitted_total"] > 0
    assert len(parsed) > 4


def test_answer_just_over_on_the_wire_only_is_cut():
    """Under the cap as a raw string, over it as received."""
    cap = TOOL_CAPS["file_summary"].max_bytes
    n = 1
    while len(dumps_answer({"rows": [{"text": _ESCAPY}] * (n + 1)}).encode()) <= cap:
        n += 1
    payload = {"rows": [{"text": _ESCAPY} for _ in range(n)]}
    raw = dumps_answer(payload)
    assert len(raw.encode()) <= cap < wire_bytes(raw)
    out = cap_tool_answer("file_summary", raw, {})
    assert wire_bytes(out) <= cap


def test_a_large_json_list_fits_on_the_wire():
    raw = json.dumps([_ESCAPY] * 500)
    out = cap_tool_answer("some_tool", raw, {})
    assert wire_bytes(out) <= ANSWER_MAX_BYTES
    assert json.loads(out)["items"][0] == _ESCAPY


def test_cap_answer_measures_the_wire():
    payload = {"rows": [{"text": _ESCAPY} for _ in range(400)]}
    assert cap_answer(payload, ["rows"], "Narrow it.", max_bytes=3_000)
    assert wire_bytes(dumps_answer(payload)) <= 3_000


def test_hard_cap_cuts_when_the_structured_cap_fails(monkeypatch):
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    def boom(*a, **k):
        raise RuntimeError("cap failed")

    monkeypatch.setattr("codegraph.server._budget.cap_tool_answer", boom)

    def find_callers() -> str:
        return "\n".join(f'"{_ESCAPY}"' for _ in range(1000))

    out = _srv._logged_tool(find_callers)()
    assert wire_bytes(out) <= ANSWER_MAX_BYTES


# ---------------------------------------------------------------------------
# Tool-specific fits
# ---------------------------------------------------------------------------


def test_file_summary_fit_holds_on_the_wire():
    cap = TOOL_CAPS["file_summary"].max_bytes
    payload = {
        "file": "src/app.py",
        "module_doc": _ESCAPY * 10,
        "functions": [
            {"name": f"handler_{i}", "lines": f"{i * 10}-{i * 10 + 8}", "doc": _ESCAPY}
            for i in range(40)
        ],
        "classes": [],
        "imports": [f'src/"dep"_{i}.py' for i in range(20)],
        "imported_by": [f"src/user_{i}.py" for i in range(20)],
    }
    _fit_summary(payload, cap)
    assert wire_bytes(dumps_answer(payload)) <= cap


def test_knowledge_search_fit_holds_on_the_wire():
    payload = {
        "query": "x",
        "hits": [
            {"id": i, "title": f"Note {i}", "body": (_ESCAPY + _ACCENTS) * 8}
            for i in range(10)
        ],
    }
    _fit_search_answer(payload, drop_hits=True)
    assert wire_bytes(dumps_answer(payload)) <= SEARCH_TARGET_BYTES


def test_knowledge_list_fit_holds_on_the_wire():
    payload = {
        "offset": 0,
        "entries": [
            {"id": i, "title": f'"Note" {i}', "body": _ESCAPY * 4} for i in range(30)
        ],
    }
    _fit_list_answer(payload)
    assert wire_bytes(dumps_answer(payload)) <= LIST_TARGET_BYTES
