"""[tools] expose: tools/list narrows, tools/call never does."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from cgh.core import config as config_mod
from cgh.core.config import load_config
from cgh.core.tool_exposure import CORE_TOOLS, compute_exposure
from cgh.server.tool_exposure import (
    ToolExposureMiddleware,
    registered_tool_names,
)


@pytest.fixture(autouse=True)
def _no_global_config(tmp_path, monkeypatch):
    # A [tools] table in the developer's ~/.cgh/config.toml must not
    # leak into these tests.
    monkeypatch.setattr(config_mod, "GLOBAL_DIR", tmp_path / "global-home")


def _write_config(root: Path, body: str) -> None:
    cg = root / ".cgh"
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


def _client(server: FastMCP, client_name: str | None) -> Client:
    # client_name lets a test present itself as Claude Code or another client,
    # which is what expose = "auto" keys off. None is the fastmcp default ("mcp").
    if client_name is None:
        return Client(server)
    from mcp.types import Implementation

    return Client(server, client_info=Implementation(name=client_name, version="0"))


async def _list(server: FastMCP, client_name: str | None = None) -> set:
    # Through a client, as an agent sees it: works on every supported fastmcp.
    async with _client(server, client_name) as client:
        return {t.name for t in await client.list_tools()}


def _client_name_observable() -> bool:
    """Whether the in-process client's reported name reaches the server on
    this fastmcp. The oldest floor does not carry clientInfo in-process, so
    "auto" cannot tell a Claude client apart there; real Claude Code over
    stdio does send it. The per-client tests that need it skip when it can't
    be observed, rather than assert behaviour the harness cannot set up."""
    from fastmcp.server.middleware import Middleware

    from cgh.server.tool_exposure import _client_name

    probe = FastMCP(name="probe")

    @probe.tool
    def _ping() -> str:
        return "pong"

    seen: dict[str, str | None] = {}

    class _Probe(Middleware):
        async def on_list_tools(self, context, call_next):
            seen["name"] = _client_name(context)
            return await call_next(context)

    probe.add_middleware(_Probe())
    try:
        asyncio.run(_list(probe, client_name="claude-code"))
    except Exception:
        return False
    return seen.get("name") == "claude-code"


_CLIENT_NAME_OBSERVABLE = _client_name_observable()
_needs_client_name = pytest.mark.skipif(
    not _CLIENT_NAME_OBSERVABLE,
    reason="this fastmcp does not carry clientInfo to an in-process server",
)


async def _list_and_call(
    server: FastMCP, name: str, args: dict, client_name: str | None = None
) -> tuple[set, str]:
    async with _client(server, client_name) as client:
        listed = {t.name for t in await client.list_tools()}
        result = await client.call_tool(name, args)
        text = result.content[0].text
    return listed, text


@_needs_client_name
def test_default_auto_lists_every_tool_for_claude(tmp_path):
    # No [tools] table: the default "auto" lists everything to a tool-search
    # client (Claude Code), which then defers the non-core tail itself.
    server, _ = _server(tmp_path)
    listed, _ = asyncio.run(
        _list_and_call(
            server, "pattern_search", {"pattern": "x"}, client_name="claude-code"
        )
    )
    assert listed == {
        "pattern_search",
        "symbol_lookup",
        "incremental_reindex",
        "find_callees",
        "codegen_pick",
    }


def test_default_auto_lists_core_for_other_clients(tmp_path):
    # The same default "auto", but a client without tool search (here the
    # fastmcp default "mcp") gets the core view so it is not flooded.
    server, _ = _server(tmp_path)
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert listed == {"pattern_search", "symbol_lookup", "find_callees", "codegen_pick"}
    assert "incremental_reindex" not in listed


@_needs_client_name
def test_default_auto_lists_every_real_tool_for_claude(tmp_path, monkeypatch):
    """The real server, with the owner's snapshot taken and no [tools]
    table: a Claude client sees exactly what is registered."""
    import cgh.server as srv

    monkeypatch.setattr(srv, "_root", tmp_path)
    names = registered_tool_names(srv.mcp)
    monkeypatch.setattr(srv.tool_exposure, "builtin_names", names)
    monkeypatch.setattr(srv.tool_exposure, "registered_names", names)
    listed = asyncio.run(_list(srv.mcp, client_name="claude-code"))
    assert listed == names
    assert names >= CORE_TOOLS  # every core name is a real tool


def test_core_lists_core_extra_and_plugin(tmp_path):
    # pattern_search, symbol_lookup and find_callees are core; incremental_reindex
    # is not, so it only shows up because it is named in extra; codegen_pick is a
    # plugin tool and always shows.
    _write_config(
        tmp_path, '[tools]\nexpose = "core"\nextra = ["incremental_reindex"]\n'
    )
    server, mw = _server(tmp_path)
    listed, _ = asyncio.run(_list_and_call(server, "pattern_search", {"pattern": "x"}))
    assert listed == {
        "pattern_search",
        "symbol_lookup",
        "find_callees",
        "incremental_reindex",
        "codegen_pick",
    }
    exposure = mw.exposure()
    assert exposure.label() == "core (3 + 1 extra + 1 plugin)"
    assert exposure.hidden == 0


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
    import cgh.server as srv

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
