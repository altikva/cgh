# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The call log records who triggered each tool call, so usage
#              data separates what agents choose from what hooks and the CLI
#              ask. Covers each path (stdio proxy, CLI client, hook entry
#              point, in-process call), the header the owner reads, the
#              migration of an existing call_log.db and the stats split.

from __future__ import annotations

import http.server
import io
import json
import sqlite3
import threading

import pytest

import codegraph.__main__ as cli_main
import codegraph.server as srv
from codegraph.cli import owner_client
from codegraph.state import call_log, ipc

_TOOL_CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "knowledge_terms", "arguments": {}},
}


@pytest.fixture(autouse=True)
def _fresh_log(monkeypatch):
    # setenv first so teardown removes whatever a hook entry point sets
    # in os.environ during the test.
    monkeypatch.setenv(call_log.ORIGIN_ENV, "")
    monkeypatch.delenv(call_log.ORIGIN_ENV)
    call_log.reset_for_tests()
    yield
    call_log.reset_for_tests()


def _rows(root) -> list[tuple]:
    db = sqlite3.connect(root / ".codegraph" / "call_log.db")
    try:
        return db.execute(
            "SELECT tool, origin, repo_root FROM call_log ORDER BY id"
        ).fetchall()
    finally:
        db.close()


class _HeaderOwner(http.server.ThreadingHTTPServer):
    """A stand-in owner that records the origin header of each request."""

    def __init__(self) -> None:
        self.origins: list[str | None] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.origins.append(self.headers.get(call_log.ORIGIN_HEADER))
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"content": [{"type": "text", "text": "{}"}]},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


@pytest.fixture
def header_owner():
    owner = _HeaderOwner()
    yield owner
    owner.shutdown()
    owner.server_close()


# -- the senders ---------------------------------------------------------------


def test_stdio_proxy_announces_agent(monkeypatch, header_owner):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_TOOL_CALL) + "\n"))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr(ipc, "_recover_owner", lambda *a, **k: None)
    # A hook-tagged environment must not leak into the agent's own calls.
    monkeypatch.setenv(call_log.ORIGIN_ENV, "hook")
    assert ipc.proxy_stdio_to_http(header_owner.server_address[1]) == 0
    assert header_owner.origins == ["agent"]


def test_cli_client_announces_cli(tmp_path, header_owner):
    reply = owner_client.call_owner_tool(
        str(tmp_path), "live_graph_stats", port=header_owner.server_address[1]
    )
    assert reply.ok
    assert header_owner.origins == ["cli"]


def test_cli_client_under_a_hook_announces_hook(tmp_path, monkeypatch, header_owner):
    monkeypatch.setenv(call_log.ORIGIN_ENV, "hook")
    owner_client.call_owner_tool(
        str(tmp_path), "incremental_reindex", port=header_owner.server_address[1]
    )
    assert header_owner.origins == ["hook"]


def test_client_origin_only_claims_cli_or_hook(monkeypatch):
    assert call_log.client_origin() == "cli"
    monkeypatch.setenv(call_log.ORIGIN_ENV, "agent")
    assert call_log.client_origin() == "cli"
    monkeypatch.setenv(call_log.ORIGIN_ENV, "HOOK")
    assert call_log.client_origin() == "hook"


@pytest.mark.parametrize(
    "command", ["_hook_checkpoint", "_bob_prompt", "_reindex_hook"]
)
def test_hook_entry_points_tag_their_owner_calls(monkeypatch, tmp_path, command):
    seen: list[str | None] = []

    def fake(_args):
        import os

        seen.append(os.environ.get(call_log.ORIGIN_ENV))

    handler = {
        "_hook_checkpoint": "cmd_hook_checkpoint",
        "_bob_prompt": "cmd_bob_prompt",
        "_reindex_hook": "cmd_reindex_hook",
    }[command]
    monkeypatch.setattr(cli_main, handler, fake)
    monkeypatch.setattr("sys.argv", ["cgh", command])
    monkeypatch.chdir(tmp_path)
    cli_main.main()
    assert seen == ["hook"]


def test_plain_cli_command_is_not_tagged_hook(monkeypatch, tmp_path):
    seen: list[str | None] = []
    monkeypatch.setattr(
        cli_main,
        "cmd_stats",
        lambda _args: seen.append(__import__("os").environ.get(call_log.ORIGIN_ENV)),
    )
    monkeypatch.setattr("sys.argv", ["cgh", "stats"])
    monkeypatch.chdir(tmp_path)
    cli_main.main()
    assert seen == [None]


# -- the owner side --------------------------------------------------------------


def test_owner_logs_the_announced_origin(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(srv, "_root", tmp_path)
    app = srv.mcp.http_app(stateless_http=True, json_response=True)
    accept = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        for extra in (
            {},
            {call_log.ORIGIN_HEADER: "agent"},
            {call_log.ORIGIN_HEADER: "hook"},
            {call_log.ORIGIN_HEADER: "cli"},
            {call_log.ORIGIN_HEADER: "something-else"},
        ):
            resp = client.post("/mcp", json=_TOOL_CALL, headers={**accept, **extra})
            assert resp.status_code == 200
    rows = _rows(tmp_path)
    assert [r[1] for r in rows] == ["agent", "agent", "hook", "cli", None]
    assert {r[2] for r in rows} == {str(tmp_path.resolve())}


def test_in_process_call_is_internal(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_root", tmp_path)

    @srv._logged_tool
    def probe() -> str:
        return "ok"

    assert probe() == "ok"
    assert _rows(tmp_path) == [("probe", "internal", str(tmp_path.resolve()))]


# -- storage -----------------------------------------------------------------------


def test_existing_call_log_is_migrated_silently(tmp_path):
    db_dir = tmp_path / ".codegraph"
    db_dir.mkdir()
    old = sqlite3.connect(db_dir / "call_log.db")
    old.execute(
        """CREATE TABLE call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
            tool TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}',
            latency_ms REAL NOT NULL DEFAULT 0, result_size INTEGER NOT NULL DEFAULT 0,
            success INTEGER NOT NULL DEFAULT 1, error TEXT)"""
    )
    old.execute(
        "INSERT INTO call_log (timestamp, tool) VALUES (1700000000, 'symbol_lookup')"
    )
    old.commit()
    old.close()

    call_log.log_call("symbol_lookup", {}, 1.0, 10, repo_root=tmp_path, origin="agent")
    call_log.log_call("scan_status", {}, 1.0, 10, repo_root=tmp_path, origin="hook")

    rows = _rows(tmp_path)
    assert rows[0] == ("symbol_lookup", None, None)  # old row stays unknown
    assert rows[1][1] == "agent" and rows[2][1] == "hook"

    stats = call_log.get_stats(tmp_path)
    assert stats["by_origin"] == {"unknown": 1, "agent": 1, "hook": 1}
    assert stats["tools"]["symbol_lookup"]["by_origin"] == {"unknown": 1, "agent": 1}
    assert stats["tools"]["symbol_lookup"]["calls"] == 2  # existing field unchanged
    assert stats["repo_root"] == str(tmp_path.resolve())
    assert stats["recent_calls"][0]["origin"] in {"agent", "hook"}
    logs = call_log.get_logs(tmp_path)
    assert {entry["origin"] for entry in logs} == {None, "agent", "hook"}


def test_unknown_origin_value_is_stored_as_null(tmp_path):
    call_log.log_call("x", {}, 1.0, 0, repo_root=tmp_path, origin="robot")
    assert _rows(tmp_path)[0][1] is None


def test_stats_cli_shows_the_origin_split(tmp_path):
    from rich.console import Console

    from codegraph.cli.commands_monitor import _origin_table

    call_log.log_call("a", {}, 1.0, 0, repo_root=tmp_path, origin="agent")
    call_log.log_call("a", {}, 1.0, 0, repo_root=tmp_path, origin="hook")
    table = _origin_table(call_log.get_stats(tmp_path)["by_origin"])
    out = io.StringIO()
    Console(file=out, width=100).print(table)
    text = out.getvalue()
    assert "agent (MCP client)" in text and "hook" in text
    assert "cli (cgh commands)" not in text
