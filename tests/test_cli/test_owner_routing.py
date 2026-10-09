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

        # The changed lines of a code file ride along with it.
        assert calls == [("impact_report", {"changed_files": ["lib.py#L2"]})]
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


# ---------------------------------------------------------------------------
# lookup / search / files / stats: owner first, same output as a local open
# ---------------------------------------------------------------------------


class _FakeMcp:
    """Stand-in for FastMCP: .tool() records the decorated function."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def read_repo(tmp_path):
    """Indexed git repo holding every kind the read commands show: functions,
    a class, a terraform resource and variable, markdown sections, and a
    file that defines no symbol (in the graph, absent from the FTS)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    files = {
        "lib.py": "def helper():\n    return 1\n\n\nclass HelperBox:\n    pass\n",
        "app.py": "from lib import helper\n\n\ndef run():\n    return helper()\n",
        "consts.py": "X = 1\n",
        "README.md": "# Guide\n\nIntro.\n\n## Using helper\n\nCall helper here.\n",
        "main.tf": (
            'resource "null_resource" "helper" {\n}\n\n'
            'variable "helper" {\n  default = 1\n}\n'
        ),
    }
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    reset_connection()
    index_repo(str(root))
    reset_connection()
    yield root
    reset_connection()


@pytest.fixture
def in_process_owner(read_repo, monkeypatch):
    """call_owner_tool answered by the real MCP tool bodies, run in-process
    against ``read_repo``: what a live owner would return. Records calls."""
    import codegraph.server as _srv
    from codegraph.server.tools_query import register as register_query
    from codegraph.server.tools_viz import register as register_viz

    mcp = _FakeMcp()
    register_query(mcp)
    register_viz(mcp)
    calls: list[tuple] = []

    def _fake(root, tool, arguments=None, **_k):
        calls.append((tool, arguments))
        return OwnerReply("ok", data=json.loads(mcp.tools[tool](**(arguments or {}))))

    # A live owner logs every call; the CLI reads the call log first, so
    # keeping the log out of the comparison only removes test noise.
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **_k: None)
    monkeypatch.setattr(_srv, "_root", read_repo.resolve())
    monkeypatch.setattr(oc, "call_owner_tool", _fake)
    yield calls
    reset_connection()


def _capture(monkeypatch, *modules) -> io.StringIO:
    buf = io.StringIO()
    console = Console(file=buf, width=200, no_color=True)
    for mod in modules:
        monkeypatch.setattr(mod, "console", console)
    return buf


def _absent(monkeypatch):
    monkeypatch.setattr(oc, "call_owner_tool", lambda *a, **k: OwnerReply("absent"))


def _older(monkeypatch):
    monkeypatch.setattr(
        oc,
        "call_owner_tool",
        lambda *a, **k: OwnerReply("unknown_tool", error="Unknown tool: 'x'"),
    )


def _stuck(monkeypatch):
    monkeypatch.setattr(
        oc,
        "call_owner_tool",
        lambda *a, **k: OwnerReply("timeout", error="no answer within 30s"),
    )


def _search_args(root, query="elp", json_out=False):
    return argparse.Namespace(
        root=str(root), query=query, text=None, limit=None, offset=0, json=json_out
    )


def _lookup_args(root, name="helper"):
    return argparse.Namespace(root=str(root), name=name)


def _files_args(root, pattern="", check="", limit=200):
    return argparse.Namespace(root=str(root), pattern=pattern, check=check, limit=limit)


def _run(monkeypatch, fn, args, *modules) -> str:
    buf = _capture(monkeypatch, *modules)
    fn(args)
    return buf.getvalue()


class TestReadCommandsParity:
    """The owner route prints byte for byte what a local open prints."""

    @pytest.fixture(autouse=True)
    def _debug_route(self, monkeypatch):
        monkeypatch.setenv("CGH_DEBUG_ROUTE", "1")

    def test_lookup(self, read_repo, in_process_owner, monkeypatch, capsys):
        args = _lookup_args(read_repo)
        _forbid_local_open(monkeypatch)
        via_owner = _run(monkeypatch, cq.cmd_lookup, args, cq)
        assert "lookup: served by owner" in capsys.readouterr().err
        monkeypatch.undo()
        reset_connection()
        _absent(monkeypatch)
        local = _run(monkeypatch, cq.cmd_lookup, args, cq)
        assert via_owner == local
        # A terraform variable is found by address, with its full line range.
        for want in ("lib.py:1-2", "main.tf:4-6", "Using helper  README.md:5-7"):
            assert want in local

    @pytest.mark.parametrize("json_out", [False, True])
    def test_search(self, read_repo, in_process_owner, monkeypatch, capsys, json_out):
        args = _search_args(read_repo, json_out=json_out)
        _forbid_local_open(monkeypatch)
        via_owner = _run(monkeypatch, cq.cmd_search, args, cq)
        captured = capsys.readouterr()
        via_owner += captured.out
        assert "search: served by owner" in captured.err
        assert in_process_owner[0][0] == "search_symbols"
        monkeypatch.undo()
        reset_connection()
        _absent(monkeypatch)
        local = _run(monkeypatch, cq.cmd_search, args, cq) + capsys.readouterr().out
        assert via_owner == local
        assert "HelperBox" in local and "Using helper" in local
        # Terraform blocks are listed too, by address, on both routes.
        assert "var.helper" in local and "null_resource.helper" in local

    @pytest.mark.parametrize(
        "kw",
        [{}, {"pattern": "s.py"}, {"limit": 2}, {"check": "consts.py"}],
    )
    def test_files(self, read_repo, in_process_owner, monkeypatch, capsys, kw):
        import codegraph.cli.commands_files as cf

        args = _files_args(read_repo, **kw)
        _forbid_local_open(monkeypatch)
        via_owner = _run(monkeypatch, cf.cmd_files, args, cf)
        assert "files: served by owner" in capsys.readouterr().err
        assert in_process_owner[0][0] == "indexed_files"
        monkeypatch.undo()
        reset_connection()
        _absent(monkeypatch)
        local = _run(monkeypatch, cf.cmd_files, args, cf)
        assert via_owner == local
        if not kw:
            # consts.py defines no symbol: only the graph knows it.
            assert "consts.py" in local and "5 indexed file(s) via graph" in local
        if kw.get("check"):
            assert local.startswith("indexed  consts.py")

    def test_stats_json(self, read_repo, in_process_owner, monkeypatch, capsys):
        import codegraph.cli.commands_monitor as cm

        args = argparse.Namespace(root=str(read_repo), json=True, live=False)
        _forbid_local_open(monkeypatch)
        cm.cmd_stats(args)
        captured = capsys.readouterr()
        via_owner = json.loads(captured.out)
        assert "stats: served by owner" in captured.err
        monkeypatch.undo()
        reset_connection()
        _absent(monkeypatch)
        cm.cmd_stats(args)
        local = json.loads(capsys.readouterr().out)
        assert via_owner == local
        assert local["graph"]["nodes"]["File"] == 5
        assert local["graph"]["edges"]["DEFINES_FN"] == 2

    def test_stats_table(self, read_repo, in_process_owner, monkeypatch, capsys):
        import codegraph.cli.commands_monitor as cm

        args = argparse.Namespace(root=str(read_repo), json=False, live=False)
        _forbid_local_open(monkeypatch)
        via_owner = _run(monkeypatch, cm.cmd_stats, args, cm)
        assert "stats: served by owner" in capsys.readouterr().err
        monkeypatch.undo()
        reset_connection()
        _absent(monkeypatch)
        local = _run(monkeypatch, cm.cmd_stats, args, cm)
        assert via_owner == local
        assert "Graph Nodes" in local and "Graph Edges" in local


class TestReadCommandsRouting:
    def test_search_older_owner_uses_fts_without_graph_open(
        self, read_repo, monkeypatch
    ):
        _older(monkeypatch)
        _forbid_local_open(monkeypatch)
        out = _run(monkeypatch, cq.cmd_search, _search_args(read_repo), cq)
        assert "helper" in out and "older cgh" not in out

    def test_search_owner_without_name_only_echo_uses_fts(self, read_repo, monkeypatch):
        # An older owner ignores the new arguments and answers its own way.
        data = {"query": "elp", "results": [{"kind": "tf_resource", "name": "x"}]}
        monkeypatch.setattr(
            oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
        )
        _forbid_local_open(monkeypatch)
        out = _run(monkeypatch, cq.cmd_search, _search_args(read_repo), cq)
        assert "HelperBox" in out

    def test_lookup_older_owner_uses_fts_without_graph_open(
        self, read_repo, monkeypatch
    ):
        _older(monkeypatch)
        _forbid_local_open(monkeypatch)
        out = _run(monkeypatch, cq.cmd_lookup, _lookup_args(read_repo), cq)
        assert "lib.py:1-2" in out

    def test_lookup_owner_without_names_uses_fts(self, read_repo, monkeypatch):
        data = {
            "found": True,
            "definitions": [{"kind": "md_section", "file": "x.md", "lines": "1-2"}],
        }
        monkeypatch.setattr(
            oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
        )
        _forbid_local_open(monkeypatch)
        out = _run(monkeypatch, cq.cmd_lookup, _lookup_args(read_repo), cq)
        assert "x.md" not in out and "lib.py:1-2" in out

    def test_files_older_owner_uses_fts_without_graph_open(
        self, read_repo, monkeypatch
    ):
        import codegraph.cli.commands_files as cf

        _older(monkeypatch)
        _forbid_local_open(monkeypatch)
        out = _run(monkeypatch, cf.cmd_files, _files_args(read_repo), cf)
        assert "lib.py" in out and "via FTS" in out

    def test_stats_older_owner_reports_lock_without_graph_open(
        self, read_repo, monkeypatch, capsys
    ):
        import codegraph.cli.commands_monitor as cm

        _older(monkeypatch)
        _forbid_local_open(monkeypatch)
        cm.cmd_stats(argparse.Namespace(root=str(read_repo), json=True, live=False))
        out = json.loads(capsys.readouterr().out)
        assert out["graph"] == {"nodes": {}, "edges": {}}
        assert out["fts"]["indexed_symbols"] > 0

    def test_stats_owner_without_edges_keeps_node_counts(
        self, read_repo, monkeypatch, capsys
    ):
        import codegraph.cli.commands_monitor as cm

        data = {"nodes": {"File": 5, "Function": 2}, "nodes_total": 7}
        monkeypatch.setattr(
            oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
        )
        _forbid_local_open(monkeypatch)
        cm.cmd_stats(argparse.Namespace(root=str(read_repo), json=True, live=False))
        out = json.loads(capsys.readouterr().out)
        assert out["graph"] == {"nodes": {"File": 5, "Function": 2}, "edges": {}}

    @pytest.mark.parametrize("command", ["lookup", "search", "files", "check"])
    def test_stuck_owner_exits_with_hint(self, read_repo, monkeypatch, command):
        import codegraph.cli.commands_files as cf

        _stuck(monkeypatch)
        _forbid_local_open(monkeypatch)
        buf = _capture(monkeypatch, cq, cf)
        fn, args = {
            "lookup": (cq.cmd_lookup, _lookup_args(read_repo)),
            "search": (cq.cmd_search, _search_args(read_repo)),
            "files": (cf.cmd_files, _files_args(read_repo)),
            "check": (cf.cmd_files, _files_args(read_repo, check="lib.py")),
        }[command]
        with pytest.raises(SystemExit) as exc:
            fn(args)
        assert exc.value.code == 1
        assert "cgh doctor --owner" in buf.getvalue()

    def test_stats_stuck_owner_exits_with_json_error(
        self, read_repo, monkeypatch, capsys
    ):
        import codegraph.cli.commands_monitor as cm

        _stuck(monkeypatch)
        _forbid_local_open(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cm.cmd_stats(argparse.Namespace(root=str(read_repo), json=True, live=False))
        assert exc.value.code == 1
        assert "cgh doctor --owner" in json.loads(capsys.readouterr().out)["error"]

    def test_owner_absent_reads_locally(self, read_repo, monkeypatch):
        import codegraph.cli.commands_files as cf

        _absent(monkeypatch)
        out = _run(monkeypatch, cf.cmd_files, _files_args(read_repo), cf)
        assert "5 indexed file(s) via graph" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner lifecycle")
def test_read_commands_with_live_owner(read_repo):
    """lookup / search / files / stats answer through a real owner, well under
    DuckDB's lock retry (left at its default here), and print what a local
    open prints once the owner is gone."""
    import time

    from codegraph.state.ipc import (
        register_keepalive,
        spawn_owner,
        stop_owner,
        unregister_keepalive,
    )

    root = str(read_repo)
    env = {k: v for k, v in os.environ.items() if k != "CGH_LOCK_WAIT"}
    env.update({"CGH_NO_PROGRESS": "1", "CGH_DEBUG_ROUTE": "1"})
    commands = {
        "lookup": ["lookup", "helper"],
        "search": ["search", "elp", "--json"],
        "files": ["files"],
        "check": ["files", "--check", "consts.py"],
        "stats": ["stats", "--json"],
    }

    def _cgh(argv):
        t0 = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "codegraph", *argv, "--root", root],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        return result, time.monotonic() - t0

    via_owner: dict = {}
    register_keepalive(read_repo)
    try:
        port = spawn_owner(read_repo, watch=False, reindex=False)
        assert port, "owner did not come up"
        warm = oc.call_owner_tool(root, "live_graph_stats", timeout=30)
        assert warm.ok, warm.error
        for name, argv in commands.items():
            result, elapsed = _cgh(argv)
            assert result.returncode == 0, result.stdout + result.stderr
            verb = "files" if name == "check" else name
            assert f"{verb}: served by owner" in result.stderr, result.stderr
            assert elapsed < 5, f"{name} took {elapsed:.1f}s"
            via_owner[name] = result.stdout
    finally:
        unregister_keepalive(read_repo)
        stop_owner(read_repo)

    assert "Using helper  README.md:5-7" in via_owner["lookup"]
    names = {r["name"] for r in json.loads(via_owner["search"])["results"]}
    assert names == {
        "helper",
        "HelperBox",
        "Using helper",
        "null_resource.helper",
        "var.helper",
    }
    assert "consts.py" in via_owner["files"]
    assert via_owner["check"].startswith("indexed  consts.py")
    graph = json.loads(via_owner["stats"])["graph"]
    assert graph["nodes"]["File"] == 5 and graph["edges"]["DEFINES_FN"] == 2

    reset_connection()
    for name, argv in commands.items():
        result, _ = _cgh(argv)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "served by local read-only open" in result.stderr
        if name == "stats":
            # The call log and storage sizes moved while the owner ran.
            assert json.loads(result.stdout)["graph"] == graph
        else:
            assert result.stdout == via_owner[name], name


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_read_commands_with_half_dead_owner(read_repo):
    """A stuck owner (SIGSTOP: pid alive, port accepting, nothing answers)
    makes every read command exit 1 with the stuck hint within
    CGH_OWNER_TIMEOUT; a dead owner whose pid and port files were left behind
    is skipped at once for the local open. Nothing may hang."""
    import signal
    import time

    from codegraph.state.ipc import (
        read_owner_pid,
        read_owner_port,
        register_keepalive,
        spawn_owner,
        unregister_keepalive,
    )

    root = str(read_repo)
    env = {k: v for k, v in os.environ.items() if k != "CGH_LOCK_WAIT"}
    env.update(
        {"CGH_NO_PROGRESS": "1", "CGH_DEBUG_ROUTE": "1", "CGH_OWNER_TIMEOUT": "1"}
    )
    commands = {
        "lookup": ["lookup", "helper"],
        "search": ["search", "elp"],
        "files": ["files"],
        "stats": ["stats", "--json"],
        "callers": ["callers", "helper"],
        "graph": ["graph", "calls", "--symbol", "helper", "--mermaid"],
    }

    def _cgh(argv):
        t0 = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "codegraph", *argv, "--root", root],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return result, time.monotonic() - t0

    register_keepalive(read_repo)
    pid = None
    try:
        assert spawn_owner(read_repo, watch=False, reindex=False), "no owner"
        pid = read_owner_pid(read_repo)
        assert oc.call_owner_tool(root, "live_graph_stats", timeout=30).ok
        os.kill(pid, signal.SIGSTOP)
        for name, argv in commands.items():
            result, elapsed = _cgh(argv)
            assert result.returncode == 1, (name, result.stdout + result.stderr)
            assert "cgh doctor --owner" in result.stdout + result.stderr, name
            assert elapsed < 15, f"{name} took {elapsed:.1f}s"

        os.kill(pid, signal.SIGKILL)
        os.kill(pid, signal.SIGCONT)
        os.waitpid(pid, 0)
        pid = None
        assert read_owner_pid(read_repo) and read_owner_port(read_repo)
        for name, argv in commands.items():
            result, elapsed = _cgh(argv)
            assert result.returncode == 0, (name, result.stdout + result.stderr)
            if name != "graph":
                assert "served by local read-only open" in result.stderr, name
            assert elapsed < 15, f"{name} took {elapsed:.1f}s"
    finally:
        unregister_keepalive(read_repo)
        if pid is not None:
            for sig in (signal.SIGKILL, signal.SIGCONT):
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
