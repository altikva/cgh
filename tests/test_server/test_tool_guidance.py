# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Agents pick tools from their descriptions and the server
#              instructions. An IBM Bob session searched the code with
#              knowledge_search for a whole task, so the guide must reach
#              every agent and must name only tools that exist.

from __future__ import annotations

import asyncio
import re

import codegraph.server as srv
from codegraph.integrations.skill_installer import _USAGE_BODY
from codegraph.integrations.tool_guide import SEARCH_GUIDE


def _tools() -> dict[str, str]:
    if hasattr(srv.mcp, "list_tools"):
        tools = asyncio.run(srv.mcp.list_tools())
        return {t.name: t.description or "" for t in tools}
    return {n: t.description or "" for n, t in asyncio.run(srv.mcp.get_tools()).items()}


def test_guide_reaches_server_instructions_and_installed_rules() -> None:
    assert SEARCH_GUIDE in (srv.mcp.instructions or "")
    assert SEARCH_GUIDE in _USAGE_BODY


def test_guide_names_only_registered_tools() -> None:
    tools = _tools()
    named = set(re.findall(r"\b([a-z_]+)\(", SEARCH_GUIDE))
    missing = named - set(tools)
    assert not missing, f"guide names unknown tools: {sorted(missing)}"


def test_knowledge_tools_say_they_do_not_search_code() -> None:
    tools = _tools()
    for name in ("knowledge_search", "knowledge_list"):
        text = " ".join(tools[name].split()).lower()
        assert "not the code" in text, name
        assert "fts_search" in text, name


def test_fts_search_says_it_takes_a_sentence() -> None:
    text = " ".join(_tools()["fts_search"].split()).lower()
    assert "sentence" in text and "code" in text


def test_every_description_opens_with_the_question_it_answers() -> None:
    for name, text in _tools().items():
        first = text.strip().splitlines()[0]
        assert first.endswith("?"), f"{name}: {first!r}"


def _summary(text: str) -> str:
    # fastmcp 3+ moves an `Args:` / `Returns:` block into the input schema;
    # fastmcp 2.x (the declared floor) keeps it in the description. Measure
    # the summary we write, the part every fastmcp version shows the same.
    return re.split(r"\n\s*(?:Args|Returns):", text, maxsplit=1)[0].strip()


def test_descriptions_stay_short() -> None:
    # Every session loads every description: keep the total in check.
    tools = {n: _summary(t) for n, t in _tools().items()}
    assert sum(len(t) for t in tools.values()) < 15000
    long = {n: len(t) for n, t in tools.items() if len(t) > 450}
    assert not long, long


def test_rewrites_keep_the_safety_caveats() -> None:
    tools = {n: " ".join(t.split()) for n, t in _tools().items()}
    assert "per scope" in tools["find_dead_code"]
    assert "confirmed=False" in tools["force_index"]
    assert "confirmed=True" in tools["force_index"]
    assert "allow_fetch" in tools["fetch_and_index"]
    for name in ("scan_repo", "index_changed_files", "memory_rescan", "plan_rescan"):
        text = tools[name].lower()
        assert "rarely" in text or "last resort" in text, name
