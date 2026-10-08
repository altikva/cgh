# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI graph queries route through a live owner, which holds the
#              graph DB for writing, and fall back to a local read-only open
#              when no owner answers. Unit tests mock the HTTP call; the
#              integration test starts a real owner on a tmp repo, runs
#              `cgh impact` as a subprocess and always stops the owner.

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import threading

import pytest
from rich.console import Console

import codegraph.cli.commands_query as cq
import codegraph.cli.owner_client as oc
from codegraph.cli.commands_impact import cmd_impact
from codegraph.cli.owner_client import OwnerReply
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def impact_repo(tmp_path):
    """Indexed git repo with two commits; the second changes lib.py, which
    app.py and test_lib.py import."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "from lib import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    (root / "test_lib.py").write_text(
        "from lib import helper\n\n\ndef test_helper():\n    assert helper() == 1\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    (root / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    _git(root, "commit", "-am", "tweak lib")

    reset_connection()
    index_repo(str(root))
    reset_connection()
    yield root
    reset_connection()


def _impact_args(root) -> argparse.Namespace:
    return argparse.Namespace(root=str(root), since="HEAD~1", json=True, format="md")


def _forbid_local_open(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("local read-only open must not run")

    monkeypatch.setattr("codegraph.core.db.get_readonly_connection", _boom)


# ---------------------------------------------------------------------------
# owner_client
# ---------------------------------------------------------------------------


class TestOwnerClient:
    def test_absent_owner_sends_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(oc, "live_owner_port", lambda _root: None)
        reply = oc.call_owner_tool(str(tmp_path), "find_callers", {"fn_name": "x"})
        assert reply.status == "absent"

    def test_parses_json_envelope(self):
        raw = json.dumps(
            {"result": {"content": [{"type": "text", "text": '{"fn": "x"}'}]}}
        )
        reply = oc._parse_tool_result(raw)
        assert reply.ok and reply.data == {"fn": "x"}

    def test_parses_sse_envelope(self):
        env = {"result": {"content": [{"type": "text", "text": '{"a": 1}'}]}}
        raw = f"event: message\ndata: {json.dumps(env)}\n\n"
        assert oc._parse_tool_result(raw).data == {"a": 1}

    def test_tool_error_is_an_error(self):
        raw = json.dumps(
            {
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "fn_name is required"}],
                }
            }
        )
        reply = oc._parse_tool_result(raw)
        assert reply.status == "error" and "fn_name is required" in reply.error

    @pytest.mark.parametrize(
        "text",
        [
            "Unknown tool: 'impact_report'",  # FastMCP
            "Unknown tool: impact_report",  # low-level MCP SDK
        ],
    )
    def test_unknown_tool_result_is_its_own_status(self, text):
        raw = json.dumps(
            {"result": {"isError": True, "content": [{"type": "text", "text": text}]}}
        )
        reply = oc._parse_tool_result(raw)
        assert reply.status == "unknown_tool" and "impact_report" in reply.error

    def test_unknown_tool_jsonrpc_error_is_its_own_status(self):
        raw = json.dumps(
            {"error": {"code": -32602, "message": "Unknown tool: impact_report"}}
        )
        assert oc._parse_tool_result(raw).status == "unknown_tool"

    def test_error_mentioning_unknown_tool_later_stays_an_error(self):
        raw = json.dumps(
            {
                "result": {
                    "isError": True,
                    "content": [
                        {"type": "text", "text": "query failed: Unknown tool table"}
                    ],
                }
            }
        )
        assert oc._parse_tool_result(raw).status == "error"

    def test_silent_owner_times_out(self, tmp_path):
        # A listening socket that never answers stands in for a wedged owner.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        held: list[socket.socket] = []
        stop = threading.Event()

        def _accept_and_hold():
            srv.settimeout(5)
            try:
                conn, _ = srv.accept()
                held.append(conn)
                stop.wait(5)
            except OSError:
                pass

        t = threading.Thread(target=_accept_and_hold, daemon=True)
        t.start()
        try:
            reply = oc.call_owner_tool(
                str(tmp_path), "find_callers", port=srv.getsockname()[1], timeout=0.3
            )
        finally:
            stop.set()
            t.join(2)
            for c in held:
                c.close()
            srv.close()
        assert reply.status == "timeout"
        hint = oc.stuck_owner_hint(str(tmp_path), reply)
        assert "cgh doctor --owner" in hint and "cgh stop" in hint

    def test_timeout_env_override(self, monkeypatch):
        monkeypatch.setenv("CGH_OWNER_TIMEOUT", "2.5")
        assert oc.owner_timeout() == 2.5
        monkeypatch.setenv("CGH_OWNER_TIMEOUT", "nonsense")
        assert oc.owner_timeout() == oc._DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# cgh impact routing
# ---------------------------------------------------------------------------


class TestImpactRouting:
    def test_owner_alive_serves_the_report(self, impact_repo, monkeypatch, capsys):
        calls: list[tuple] = []
        fake = {"since_changed": ["lib.py"], "impacted": [], "note": "n"}

        def _fake(root, tool, arguments=None, **_k):
            calls.append((tool, arguments))
            return OwnerReply("ok", data=dict(fake))

        monkeypatch.setattr(oc, "call_owner_tool", _fake)
        _forbid_local_open(monkeypatch)

        cmd_impact(_impact_args(impact_repo))

        assert calls == [("impact_report", {"changed_files": ["lib.py"]})]
        report = json.loads(capsys.readouterr().out)
        assert report == {**fake, "since": "HEAD~1"}

    def test_owner_absent_opens_locally(self, impact_repo, monkeypatch, capsys):
        monkeypatch.setattr(oc, "call_owner_tool", lambda *a, **k: OwnerReply("absent"))
        cmd_impact(_impact_args(impact_repo))
        report = json.loads(capsys.readouterr().out)
        assert report["since_changed"] == ["lib.py"]
        assert {r["file"] for r in report["impacted"]} == {"app.py", "test_lib.py"}
        assert [t["file"] for t in report["tests_to_run"]] == ["test_lib.py"]

    def test_owner_error_falls_back_to_local(self, impact_repo, monkeypatch, capsys):
        # Any tool error other than "unknown tool" keeps the local fallback.
        monkeypatch.setattr(
            oc,
            "call_owner_tool",
            lambda *a, **k: OwnerReply("error", error="graph query failed"),
        )
        cmd_impact(_impact_args(impact_repo))
        assert json.loads(capsys.readouterr().out)["since_changed"] == ["lib.py"]

    def test_older_owner_fails_fast_without_local_open(
        self, impact_repo, monkeypatch, capsys
    ):
        # An owner started by an older cgh has no impact_report tool but still
        # holds the graph: a local open would only wait on the lock and fail.
        monkeypatch.setattr(
            oc,
            "call_owner_tool",
            lambda *a, **k: OwnerReply(
                "unknown_tool", error="Unknown tool: 'impact_report'"
            ),
        )
        _forbid_local_open(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cmd_impact(_impact_args(impact_repo))
        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().out)["error"]
        assert "older cgh" in err and "cgh stop" in err and "impact_report" in err

    def test_stuck_owner_fails_fast_without_local_open(
        self, impact_repo, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            oc,
            "call_owner_tool",
            lambda *a, **k: OwnerReply("timeout", error="no answer within 30s"),
        )
        _forbid_local_open(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cmd_impact(_impact_args(impact_repo))
        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().out)["error"]
        assert "cgh doctor --owner" in err and "cgh stop" in err

    def test_owner_and_local_paths_agree(self, impact_repo, monkeypatch, capsys):
        # The impact_report tool and the local path share one builder.
        from codegraph.analysis.impact import build_impact_report
        from codegraph.core.db import get_readonly_connection

        conn = get_readonly_connection(str(impact_repo))
        via_tool = build_impact_report(conn, str(impact_repo), ["lib.py"])
        monkeypatch.setattr(oc, "call_owner_tool", lambda *a, **k: OwnerReply("absent"))
        cmd_impact(_impact_args(impact_repo))
        local = json.loads(capsys.readouterr().out)
        local.pop("since")
        assert local == via_tool


# ---------------------------------------------------------------------------
# callers / callees / outline routing
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_console(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cq, "console", Console(file=buf, width=200, no_color=True))
    return buf


class TestQueryRouting:
    def test_callers_via_owner(self, tmp_path, monkeypatch, captured_console):
        data = {
            "fn": "helper",
            "callers": [{"caller": "run", "file": str(tmp_path / "app.py"), "line": 4}],
        }
        monkeypatch.setattr(
            oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
        )
        _forbid_local_open(monkeypatch)
        cq.cmd_callers(argparse.Namespace(root=str(tmp_path), fn_name="helper"))
        assert "run" in captured_console.getvalue()
        assert "app.py:4" in captured_console.getvalue()

    def test_callers_owner_absent_opens_locally(
        self, impact_repo, monkeypatch, captured_console
    ):
        monkeypatch.setattr(oc, "call_owner_tool", lambda *a, **k: OwnerReply("absent"))
        cq.cmd_callers(argparse.Namespace(root=str(impact_repo), fn_name="helper"))
        assert "test_helper" in captured_console.getvalue()

    def test_callees_via_owner(self, tmp_path, monkeypatch, captured_console):
        data = {
            "fn": "run",
            "callees": [
                {"callee": "helper", "file": str(tmp_path / "lib.py"), "line": 1}
            ],
        }
        seen: list = []

        def _fake(root, tool, arguments=None, **_k):
            seen.append((tool, arguments))
            return OwnerReply("ok", data=data)

        monkeypatch.setattr(oc, "call_owner_tool", _fake)
        _forbid_local_open(monkeypatch)
        cq.cmd_callees(argparse.Namespace(root=str(tmp_path), fn_name="run"))
        assert seen == [("find_callees", {"fn_name": "run", "max_depth": 1})]
        assert "helper" in captured_console.getvalue()

    def test_outline_via_owner(self, tmp_path, monkeypatch, captured_console):
        data = {
            "outline": [
                {"scope": "parent", "title": "Guide", "level": 1, "line": 1},
                {"scope": "parent", "title": "Usage", "level": 2, "line": 3},
            ]
        }
        monkeypatch.setattr(
            oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
        )
        _forbid_local_open(monkeypatch)
        cq.cmd_outline(argparse.Namespace(root=str(tmp_path), file="README.md"))
        out = captured_console.getvalue()
        assert "Guide" in out and "Usage L3" in out

    def test_stuck_owner_exits_with_hint(self, tmp_path, monkeypatch, captured_console):
        monkeypatch.setattr(
            oc,
            "call_owner_tool",
            lambda *a, **k: OwnerReply("timeout", error="no answer within 30s"),
        )
        _forbid_local_open(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cq.cmd_callers(argparse.Namespace(root=str(tmp_path), fn_name="x"))
        assert exc.value.code == 1
        assert "cgh doctor --owner" in captured_console.getvalue()

    def test_older_owner_exits_with_hint(self, tmp_path, monkeypatch, captured_console):
        monkeypatch.setattr(
            oc,
            "call_owner_tool",
            lambda *a, **k: OwnerReply("unknown_tool", error="Unknown tool: 'x'"),
        )
        _forbid_local_open(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cq.cmd_callers(argparse.Namespace(root=str(tmp_path), fn_name="x"))
        assert exc.value.code == 1
        out = captured_console.getvalue()
        assert "older cgh" in out and "cgh stop" in out


# ---------------------------------------------------------------------------
# Integration: a real owner holds the graph, `cgh impact` still answers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner lifecycle")
def test_impact_cli_with_live_owner(impact_repo):
    from codegraph.state.ipc import (
        register_keepalive,
        spawn_owner,
        stop_owner,
        unregister_keepalive,
    )

    root = str(impact_repo)
    env = {
        **os.environ,
        "CGH_NO_PROGRESS": "1",
        "CGH_DEBUG_ROUTE": "1",
        "CGH_LOCK_WAIT": "0",
    }
    register_keepalive(impact_repo)
    try:
        port = spawn_owner(impact_repo, watch=False, reindex=False)
        assert port, "owner did not come up"
        # Make the owner open its write connection, as any agent call does.
        warm = oc.call_owner_tool(root, "live_graph_stats", timeout=30)
        assert warm.ok, warm.error
        # The real server's answer to a tool it does not have, as an owner
        # from an older cgh gives for impact_report.
        missing = oc.call_owner_tool(root, "no_such_tool_for_test", timeout=30)
        assert missing.status == "unknown_tool", missing

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "codegraph",
                "impact",
                "--since",
                "HEAD~1",
                "--json",
                "--root",
                root,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "impact: served by owner" in result.stderr
        report = json.loads(result.stdout)
        assert report["since_changed"] == ["lib.py"]
        assert {r["file"] for r in report["impacted"]} == {"app.py", "test_lib.py"}
        assert [t["file"] for t in report["tests_to_run"]] == ["test_lib.py"]
    finally:
        unregister_keepalive(impact_repo)
        stop_owner(impact_repo)
