# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: the knowledge tools take entry_id as an integer (how search and
#              list answers show an id) or as its decimal string, through the
#              MCP argument validation and not only in the function body. A
#              value that is not a number answers a clear error and changes
#              nothing, and a shared-store id stays refused.

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client, FastMCP

import cgh.server as _srv
from cgh.server.tools_knowledge import _repo_entry_id, register
from cgh.state.call_log import (
    knowledge_get,
    knowledge_outcome_scores,
    knowledge_record,
    reset_for_tests,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    reset_for_tests()
    # Isolate the global dir so the default fleet read (subscribed shared
    # stores) can never reach the machine's real ~/.cgh/shared during tests.
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    (tmp_path / ".cgh").mkdir()
    monkeypatch.setattr(_srv, "_root", tmp_path)
    yield tmp_path
    reset_for_tests()


@pytest.fixture
def server(store) -> FastMCP:
    # A real FastMCP: the argument schema is what refused an integer, and a
    # direct call of the tool function never meets it.
    mcp = FastMCP(name="knowledge-entry-id-test")
    register(mcp)
    return mcp


def _call(server: FastMCP, name: str, args: dict) -> dict:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            return result.content[0].text

    return json.loads(asyncio.run(run()))


def _note(store, n: int) -> int:
    return knowledge_record(
        f"gotcha number {n} on a stale owner lock",
        f"body of note {n}",
        kind="gotcha",
        tags="papercut,lock",
        repo_root=store,
    )


def test_list_reads_the_same_entry_from_an_integer_and_a_string(store, server):
    ids = [_note(store, i) for i in range(3)]
    as_int = _call(server, "knowledge_list", {"entry_id": ids[1]})
    as_str = _call(server, "knowledge_list", {"entry_id": str(ids[1])})
    assert as_int == as_str
    assert as_int["entry_id"] == ids[1]
    assert [e["id"] for e in as_int["entries"]] == [ids[1]]
    assert as_int["entries"][0]["body"] == "body of note 1"


def test_list_refuses_a_value_that_is_not_a_number(store, server):
    _note(store, 0)
    out = _call(server, "knowledge_list", {"entry_id": "latest"})
    assert "invalid entry_id: 'latest'" in out["error"]
    assert "integer id" in out["error"]
    # No fallback to the unfiltered listing.
    assert "entries" not in out


@pytest.mark.parametrize("value", ["latest", "1.5", "12 13", True])
def test_a_malformed_id_never_resolves_to_a_row(value):
    with pytest.raises(ValueError, match="invalid entry_id"):
        _repo_entry_id(value)


@pytest.mark.parametrize("value", [12, "12", " 12 "])
def test_an_integer_and_its_string_resolve_to_the_same_row(value):
    assert _repo_entry_id(value) == 12


def test_list_still_refuses_a_shared_store_id(store, server):
    _note(store, 0)
    out = _call(server, "knowledge_list", {"entry_id": "org-platform:1"})
    assert "shared-store id" in out["error"]
    assert "entries" not in out


def test_outcome_takes_an_integer_and_a_string(store, server):
    a, b = _note(store, 0), _note(store, 1)
    by_int = _call(server, "knowledge_outcome", {"verdict": "useful", "entry_id": a})
    by_str = _call(
        server, "knowledge_outcome", {"verdict": "dead_end", "entry_id": str(b)}
    )
    assert by_int["entry_id"] == a
    assert by_str["entry_id"] == b
    entries, _files = knowledge_outcome_scores(repo_root=store)
    assert set(entries) == {a, b}


def test_outcome_records_nothing_for_a_value_that_is_not_a_number(store, server):
    _note(store, 0)
    out = _call(server, "knowledge_outcome", {"verdict": "useful", "entry_id": "x1"})
    assert "invalid entry_id: 'x1'" in out["error"]
    entries, _files = knowledge_outcome_scores(repo_root=store)
    assert not entries


def test_forget_takes_an_integer_and_a_string(store, server):
    a, b, keep = _note(store, 0), _note(store, 1), _note(store, 2)
    assert _call(server, "knowledge_forget", {"entry_id": a}) == {
        "id": a,
        "forgotten": True,
    }
    assert _call(server, "knowledge_forget", {"entry_id": str(b)}) == {
        "id": b,
        "forgotten": True,
    }
    assert knowledge_get(a, repo_root=store) is None
    assert knowledge_get(b, repo_root=store) is None
    assert knowledge_get(keep, repo_root=store) is not None


def test_forget_deletes_nothing_for_a_value_that_is_not_a_number(store, server):
    a = _note(store, 0)
    out = _call(server, "knowledge_forget", {"entry_id": "all"})
    assert "invalid entry_id: 'all'" in out["error"]
    assert knowledge_get(a, repo_root=store) is not None


def test_promote_selects_the_same_entry_from_an_integer_and_a_string(store, server):
    ids = [_note(store, i) for i in range(3)]
    args = {"to": "org-platform", "dry_run": True}
    by_int = _call(server, "knowledge_promote", {**args, "entry_id": ids[1]})
    by_str = _call(server, "knowledge_promote", {**args, "entry_id": str(ids[1])})
    assert "error" not in by_int
    assert by_int == by_str
    # The selection is the one id, not the default "every durable entry".
    unfiltered = _call(server, "knowledge_promote", args)
    assert by_int != unfiltered
