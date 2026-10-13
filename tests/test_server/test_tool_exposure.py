"""[tools] expose: tools/list narrows, tools/call never does."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from codegraph.core import config as config_mod
from codegraph.core.config import load_config
from codegraph.core.tool_exposure import CORE_TOOLS, compute_exposure
from codegraph.server.tool_exposure import (
    ToolExposureMiddleware,
    registered_tool_names,
)


@pytest.fixture(autouse=True)
def _no_global_config(tmp_path, monkeypatch):
    # A [tools] table in the developer's ~/.codegraph/config.toml must not
    # leak into these tests.
    monkeypatch.setattr(config_mod, "GLOBAL_DIR", tmp_path / "global-home")


def _write_config(root: Path, body: str) -> None:
    cg = root / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    (cg / "config.toml").write_text(body, encoding="utf-8")


def _server(root: Path) -> tuple[FastMCP, ToolExposureMiddleware]:
    """A small server shaped like the owner: built-in tools, the middleware,
    the built-in snapshot, then a plugin tool registered after it."""
    server = FastMCP(name="exposure-test")

    @server.tool
    def pattern_search(pattern: str) -> str:
        return f"hits for {pattern}"

    @server.tool
    def symbol_lookup(name: str) -> str:
        return f"def {name}"

    @server.tool
    def incremental_reindex() -> str:
        return "reindexed"

    @server.tool
    def find_callees(fn: str) -> str:
        return f"callees of {fn}"

    mw = ToolExposureMiddleware(root=lambda: root)
    server.add_middleware(mw)
    builtin = registered_tool_names(server)

    @server.tool
    def codegen_pick(task: str) -> str:  # stands in for a plugin tool
        return f"picked {task}"

    mw.record(builtin, registered_tool_names(server))
    return server, mw


async def _list(server: FastMCP) -> set:
    # Through a client, as an agent sees it: works on every supported fastmcp.
    async with Client(server) as client:
        return {t.name for t in await client.list_tools()}


async def _list_and_call(server: FastMCP, name: str, args: dict) -> tuple[set, str]:
    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
        result = await client.call_tool(name, args)
        text = result.content[0].text
    return listed, text


def test_default_config_lists_every_tool(tmp_path):
    server, _ = _server(tmp_path)
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert listed == {
        "pattern_search",
        "symbol_lookup",
        "incremental_reindex",
        "find_callees",
        "codegen_pick",
    }


def test_default_config_lists_every_real_tool(tmp_path, monkeypatch):
    """The real server, with the owner's snapshot taken and no [tools]
    table: tools/list is exactly what is registered."""
    import codegraph.server as srv

    monkeypatch.setattr(srv, "_root", tmp_path)
    names = registered_tool_names(srv.mcp)
    monkeypatch.setattr(srv.tool_exposure, "builtin_names", names)
    monkeypatch.setattr(srv.tool_exposure, "registered_names", names)
    listed = asyncio.run(_list(srv.mcp))
    assert listed == names
    assert names >= CORE_TOOLS  # every core name is a real tool


def test_core_lists_core_extra_and_plugin(tmp_path):
    _write_config(tmp_path, '[tools]\nexpose = "core"\nextra = ["find_callees"]\n')
    server, mw = _server(tmp_path)
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert listed == {"pattern_search", "symbol_lookup", "find_callees", "codegen_pick"}
    exposure = mw.exposure()
    assert exposure.label() == "core (2 + 1 extra + 1 plugin)"
    assert exposure.hidden == 1


def test_hidden_tool_still_answers_call(tmp_path):
    _write_config(tmp_path, '[tools]\nexpose = "core"\n')
    server, _ = _server(tmp_path)
    listed, text = asyncio.run(_list_and_call(server, "incremental_reindex", {}))
    assert "incremental_reindex" not in listed
    assert text == "reindexed"


def test_unknown_expose_falls_back_to_all(tmp_path, caplog):
    _write_config(tmp_path, '[tools]\nexpose = "minimal"\n')
    cfg = load_config(tmp_path)
    assert cfg.tools_expose == "all"
    assert cfg.tools_expose_invalid == "minimal"
    assert any("minimal" in n for n in config_mod.config_notices(cfg))
    server, _ = _server(tmp_path)
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert "incremental_reindex" in listed
    assert len(listed) == 5


def test_unknown_extra_names_are_ignored(tmp_path, caplog):
    _write_config(
        tmp_path,
        '[tools]\nexpose = "core"\nextra = ["no_such_tool", "find_callees", 3]\n',
    )
    assert load_config(tmp_path).tools_extra == ["no_such_tool", "find_callees"]
    server, mw = _server(tmp_path)
    with caplog.at_level("WARNING"):
        listed, _ = asyncio.run(
            _list_and_call(server, "pattern_search", {"pattern": "x"})
        )
    assert "no_such_tool" not in listed
    assert "find_callees" in listed
    assert mw.exposure().unknown_extra == ("no_such_tool",)
    # Warned once, whichever logging setup the fastmcp version installs.
    assert "no_such_tool" in mw._warned_unknown


def test_malformed_extra_is_dropped(tmp_path):
    _write_config(tmp_path, '[tools]\nexpose = "core"\nextra = "find_callees"\n')
    assert load_config(tmp_path).tools_extra == []


def test_without_snapshot_everything_is_listed():
    """Before the owner records cgh's own tools, plugin tools cannot be
    told apart, so nothing is hidden."""
    exposure = compute_exposure({"pattern_search", "hotspots"}, None, "core")
    assert exposure.expose == "all"
    assert exposure.listed == {"pattern_search", "hotspots"}


def test_exposure_error_lists_every_tool(tmp_path):
    def broken_root():
        raise RuntimeError("boom")

    server, mw = _server(tmp_path)
    mw._root = broken_root
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert len(listed) == 5


def test_status_summary_on_the_real_server(tmp_path, monkeypatch):
    """What live_graph_stats hands `cgh status` under expose = "core"."""
    import codegraph.server as srv

    _write_config(tmp_path, '[tools]\nexpose = "core"\n')
    monkeypatch.setattr(srv, "_root", tmp_path)
    names = registered_tool_names(srv.mcp)
    monkeypatch.setattr(srv.tool_exposure, "builtin_names", names)
    monkeypatch.setattr(srv.tool_exposure, "registered_names", names)
    tools = srv.tool_exposure.exposure().as_dict()
    assert tools["expose"] == "core"
    assert tools["listed"] == len(CORE_TOOLS)
    assert tools["label"] == f"core ({len(CORE_TOOLS)} + 0 extra + 0 plugin)"
    json.dumps(tools)  # it rides in a JSON tool response
