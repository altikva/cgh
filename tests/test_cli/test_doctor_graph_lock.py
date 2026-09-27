# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh doctor` graph check: a graph locked by this checkout's own
#              owner is healthy and must not fail --strict; a lock with no owner
#              here still does.

from __future__ import annotations

import argparse

from codegraph.cli import commands_monitor
from codegraph.core import db
from codegraph.state import ipc


def _graph_row(tmp_path, monkeypatch, capsys, owner_alive: bool) -> str:
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "graph.duckdb").write_bytes(b"")
    monkeypatch.setattr(db, "get_readonly_connection", lambda root: None)
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: owner_alive)
    monkeypatch.setattr(ipc, "read_owner_pid", lambda root: 4242)
    monkeypatch.setattr(commands_monitor.console, "width", 200)
    args = argparse.Namespace(root=str(tmp_path), strict=False)
    commands_monitor.cmd_doctor(args)
    out = capsys.readouterr().out
    return next(line for line in out.splitlines() if "graph.duckdb" in line)


def test_lock_held_by_own_owner_is_ok(tmp_path, monkeypatch, capsys):
    row = _graph_row(tmp_path, monkeypatch, capsys, owner_alive=True)
    assert "OK" in row
    assert "pid 4242" in row


def test_lock_without_an_owner_still_fails(tmp_path, monkeypatch, capsys):
    row = _graph_row(tmp_path, monkeypatch, capsys, owner_alive=False)
    assert "locked by another process" in row
    assert "OK" not in row
