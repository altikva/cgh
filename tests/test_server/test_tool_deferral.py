# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Core tools carry the anthropic/alwaysLoad _meta so a client with
#              tool search keeps them upfront and defers the rest (issue #527,
#              option B); non-core tools carry no such marker.
from __future__ import annotations

import asyncio

from codegraph.core.tool_exposure import CORE_TOOLS


def _listed_tools():
    import codegraph.server as srv

    mcp = srv.mcp
    # Mirror registered_tool_names' version handling: the oldest fastmcp floor
    # has no list_tools, only get_tools() returning a {name: Tool} mapping.
    if hasattr(mcp, "list_tools"):
        try:
            tools = asyncio.run(mcp.list_tools(run_middleware=False))
        except TypeError:
            tools = asyncio.run(mcp.list_tools())
        return {t.name: t for t in tools}
    return dict(asyncio.run(mcp.get_tools()))


def test_core_tools_are_marked_always_load():
    tools = _listed_tools()
    for name in ("semantic_search", "pattern_search", "symbol_lookup"):
        assert name in CORE_TOOLS  # guards the test if the set changes
        assert name in tools, f"{name} not registered"
        meta = tools[name].meta or {}
        assert meta.get("anthropic/alwaysLoad") is True, (
            f"{name} should be marked alwaysLoad"
        )


def test_non_core_tools_are_not_marked():
    tools = _listed_tools()
    non_core = [n for n in tools if n not in CORE_TOOLS]
    assert non_core, "expected tools outside the core set"
    for name in non_core:
        meta = tools[name].meta or {}
        assert meta.get("anthropic/alwaysLoad") is not True, (
            f"{name} is not core and must not be marked alwaysLoad"
        )
