# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-22
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The per-tool answer ceiling applied by the shared tool
#              wrapper. Unit tests of cap_tool_answer (nested lists, long
#              strings, plain text, caller budgets), the wrapper skipping CLI
#              and hook callers, and an oversized indexed repo on which every
#              newly guarded tool answers bounded, valid, truncated JSON with
#              a hint on how to narrow.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server._budget import (
    ANSWER_MAX_BYTES,
    CONTEXT_MAX_BYTES,
    TOOL_CAPS,
    cap_tool_answer,
    wire_bytes,
)


def _big(rows: int = 400) -> dict:
    return {
        "name": "x",
        "groups": {
            "a": [{"file": f"src/a/module_{i}.py", "line": i} for i in range(rows)],
            "b": [{"file": f"src/b/module_{i}.py", "line": i} for i in range(rows)],
        },
        "items": [f"item number {i} with some padding text" for i in range(rows)],
    }


def _assert_capped(
    raw: str, ceiling: int = ANSWER_MAX_BYTES, items_dropped: bool = True
) -> dict:
    assert wire_bytes(raw) <= ceiling
    out = json.loads(raw)
    assert out["truncated"] is True
    assert (out["dropped"] > 0) is items_dropped
    assert out["hint"].startswith("Answer cut to about")
    return out


@pytest.mark.parametrize("tool", sorted(TOOL_CAPS))
def test_every_listed_tool_is_bounded(tool):
    spec = TOOL_CAPS[tool]
    payload = _big()
    for key in spec.keys:
        payload[key] = [{"id": i, "body": "b" * 80} for i in range(200)]
    out = _assert_capped(cap_tool_answer(tool, json.dumps(payload), {}), spec.max_bytes)
    if spec.hint:
        assert spec.hint in out["hint"]


def test_nested_lists_are_trimmed_evenly_from_the_tail():
    out = _assert_capped(cap_tool_answer("some_tool", json.dumps(_big()), {}))
    a, b = out["groups"]["a"], out["groups"]["b"]
    assert a[0]["line"] == 0 and b[0]["line"] == 0
    assert abs(len(a) - len(b)) <= 1
    assert out["name"] == "x"


def test_small_answer_is_returned_unchanged():
    raw = json.dumps({"rows": [1, 2, 3]}, indent=2)
    assert cap_tool_answer("find_callers", raw, {}) is raw


def test_indented_answer_that_fits_once_compact_loses_nothing():
    payload = {"rows": [{"file": f"f{i}.py", "line": i} for i in range(240)]}
    raw = json.dumps(payload, indent=2)
    assert len(raw) > ANSWER_MAX_BYTES
    capped = cap_tool_answer("find_callers", raw, {})
    assert wire_bytes(capped) <= ANSWER_MAX_BYTES
    assert json.loads(capped) == payload


def test_caller_budget_disables_the_ceiling():
    raw = json.dumps(_big())
    assert cap_tool_answer("impact_of", raw, {"max_tokens": 50_000}) is raw
    assert cap_tool_answer("resume", raw, {"budget_kb": 64}) is raw
    # Zero is "no budget": the ceiling applies.
    _assert_capped(cap_tool_answer("subgraph", raw, {"max_tokens": 0}))


def test_a_long_string_is_cut_at_a_line_break():
    text = "\n".join(f"line {i} " + "x" * 60 for i in range(400))
    raw = cap_tool_answer("architecture_report", json.dumps({"markdown": text}), {})
    out = _assert_capped(raw, items_dropped=False)
    assert out["markdown"].endswith("[cut]")
    assert text.startswith(out["markdown"][: -len("\n[cut]")])


def test_resume_keeps_the_standing_instructions():
    payload = {
        "standing_instructions": [{"id": 1, "body": "Never push to main."}],
        "digests": [{"id": i, "body": "d" * 400} for i in range(30)],
        "knowledge": [{"id": i, "body": "k" * 400} for i in range(30)],
    }
    out = _assert_capped(cap_tool_answer("resume", json.dumps(payload), {}))
    assert out["standing_instructions"] == payload["standing_instructions"]


def test_plain_text_is_cut_with_a_note():
    text = "\n".join(f"row {i}" for i in range(5000))
    raw = cap_tool_answer("some_tool", text, {})
    assert wire_bytes(raw) <= ANSWER_MAX_BYTES
    assert raw.splitlines()[-1].startswith("[Answer cut to about")


def test_a_small_json_list_is_left_alone():
    raw = json.dumps(list(range(100)))
    assert cap_tool_answer("some_tool", raw, {}) is raw


def test_a_large_json_list_is_trimmed_under_items():
    out = _assert_capped(
        cap_tool_answer("some_tool", json.dumps(list(range(10_000))), {})
    )
    assert out["items"][:3] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Through the shared tool wrapper
# ---------------------------------------------------------------------------


def _wrapped(monkeypatch, origin: str):
    monkeypatch.setattr(_srv, "_call_origin", lambda: origin)
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    @_srv._logged_tool
    def find_callers(fn_name: str) -> str:
        return json.dumps(_big())

    return find_callers


def test_wrapper_caps_an_agent_call(monkeypatch):
    _assert_capped(_wrapped(monkeypatch, "agent")(fn_name="x"))


@pytest.mark.parametrize("origin", ["cli", "hook"])
def test_wrapper_leaves_cli_and_hook_calls_whole(monkeypatch, origin):
    raw = _wrapped(monkeypatch, origin)(fn_name="x")
    assert raw == json.dumps(_big())


# ---------------------------------------------------------------------------
# Real tools on an oversized repo
# ---------------------------------------------------------------------------

_N = 320


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _write_repo(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        'def shared_target_function():\n    """The one everybody calls."""\n'
        "    return 1\n"
    )
    for i in range(_N):
        (pkg / f"helper_module_number_{i}.py").write_text(
            "from pkg.core import shared_target_function\n\n\n"
            f"def helper_function_with_a_long_name_{i}():\n"
            f'    """Helper {i} for the oversized answer fixture."""\n'
            "    return shared_target_function()\n\n\n"
            f"def orphan_function_nobody_calls_{i}():\n"
            f"    return {i}\n"
        )
    calls = "".join(
        f"    helper_module_number_{i}.helper_function_with_a_long_name_{i}()\n"
        for i in range(_N)
    )
    imports = "".join(f"from pkg import helper_module_number_{i}\n" for i in range(_N))
    (pkg / "main.py").write_text(
        imports + "\n\ndef run_every_helper():\n" + calls + "    return 0\n"
    )
    (root / "GUIDE.md").write_text(
        "".join(
            f"## Section number {i} of the guide\n\nBody of section {i}.\n\n"
            for i in range(_N)
        )
    )


@pytest.fixture
def big_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    reset_connection()
    root = tmp_path.resolve()
    _write_repo(root)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")

    from codegraph.state.call_log import knowledge_record

    for i in range(60):
        knowledge_record(
            title=f"Helper note {i}",
            body=f"helper function fixture note {i} " + "detail " * 120,
            kind="note",
            repo_root=root,
        )

    from codegraph.server import (
        _register_arch,
        _register_docs,
        _register_insight,
        _register_knowledge,
        _register_meta,
        _register_query,
        _register_tests,
    )

    m = _FakeMcp()
    for register in (
        _register_arch,
        _register_docs,
        _register_insight,
        _register_knowledge,
        _register_meta,
        _register_query,
        _register_tests,
    ):
        register(m)
    yield m.tools

    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None


_CALLS = [
    ("find_callers", {"fn_name": "shared_target_function"}),
    ("find_callees", {"fn_name": "run_every_helper"}),
    ("find_dead_code", {}),
    ("impact_of", {"symbol_or_file": "shared_target_function"}),
    ("indexed_files", {"limit": 500}),
    ("imports_of", {"file_path": "pkg/main.py"}),
    ("doc_outline", {"file_path": "GUIDE.md"}),
    ("architecture_overview", {"max_files_per_role": 500}),
    ("untested", {}),
    ("knowledge_list", {"limit": 60}),
]


@pytest.mark.parametrize(("tool", "args"), _CALLS, ids=[c[0] for c in _CALLS])
def test_oversized_tool_answer_is_bounded(big_repo, tool, args):
    raw = big_repo[tool](**args)
    out = _assert_capped(raw, TOOL_CAPS[tool].max_bytes)
    assert TOOL_CAPS[tool].hint in out["hint"]


def test_impact_of_with_max_tokens_keeps_its_own_budget(big_repo):
    raw = big_repo["impact_of"](
        symbol_or_file="shared_target_function", max_tokens=8000
    )
    assert len(raw.encode()) > ANSWER_MAX_BYTES
    json.loads(raw)


def test_knowledge_list_pages_from_the_first_entry_left_out(big_repo):
    first = _assert_capped(big_repo["knowledge_list"](limit=60))
    assert first["has_more"] is True
    assert first["next_offset"] == first["returned"] == len(first["entries"])
    second = json.loads(
        big_repo["knowledge_list"](limit=60, offset=first["next_offset"])
    )
    seen = {e["id"] for e in first["entries"]}
    assert not seen & {e["id"] for e in second["entries"]}
    assert len(seen) + len(second["entries"]) <= 60


def test_context_for_task_fits_six_kb_and_serves_the_rest_later(big_repo):
    task = "helper function with a long name for the oversized fixture"
    raw = big_repo["context_for_task"](task=task, max_nodes=80, session_id="s1")
    out = _assert_capped(raw, CONTEXT_MAX_BYTES)
    assert out["symbol_count"] >= 1
    shown = out["context_markdown"]
    # What the cut left out was not recorded as served: the next call with
    # the same session id brings new symbols.
    again = json.loads(
        big_repo["context_for_task"](task=task, max_nodes=80, session_id="s1")
    )
    assert again["symbol_count"] >= 1
    assert again["context_markdown"] != shown
