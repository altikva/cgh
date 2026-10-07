# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh doctor --owner`, the probe a supervisor polls. No owner
#              is healthy (an owner lives only while an agent needs it); the
#              one failure is an owner that is alive and does not answer.

from __future__ import annotations

import argparse
import os

import pytest

import codegraph.cli.commands_monitor as mon
import codegraph.state.index_lock as index_lock
import codegraph.state.ipc as ipc


@pytest.fixture
def owner(monkeypatch, tmp_path):
    """Fake an owner: pid 4242 on port 5555, alive, not indexing."""
    state = {"pid": 4242, "port": 5555, "alive": True, "answers": True, "asked": 0}
    monkeypatch.setattr(ipc, "read_owner_pid", lambda root: state["pid"])
    monkeypatch.setattr(ipc, "read_owner_port", lambda root: state["port"])
    monkeypatch.setattr(ipc, "is_pid_alive", lambda pid: state["alive"])
    monkeypatch.setattr(index_lock, "holder", lambda root: None)
    # No pid file on disk: the owner is past its startup window.
    monkeypatch.setattr(ipc, "owner_pidfile", lambda root: tmp_path / "absent.pid")

    def ask(root, port, timeout=1.5):
        state["asked"] += 1
        return {"files": 1} if state["answers"] else None

    monkeypatch.setattr(mon, "_ask_owner_live_stats", ask)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return state


def test_no_owner_is_healthy_and_is_never_started(owner, tmp_path):
    owner["pid"] = None
    assert mon.owner_probe(tmp_path) == (
        True,
        "no owner running (nothing to supervise)",
    )
    assert owner["asked"] == 0


def test_a_dead_pid_file_is_healthy(owner, tmp_path):
    owner["alive"] = False
    healthy, _detail = mon.owner_probe(tmp_path)
    assert healthy and owner["asked"] == 0


def test_an_answering_owner_is_healthy(owner, tmp_path):
    healthy, detail = mon.owner_probe(tmp_path)
    assert healthy and "answers on port 5555" in detail


def test_a_silent_owner_is_unhealthy_after_one_retry(owner, tmp_path):
    owner["answers"] = False
    healthy, detail = mon.owner_probe(tmp_path)
    assert not healthy and "cgh stop" in detail
    assert owner["asked"] == 2


def test_an_indexing_owner_is_not_reported_stuck(owner, tmp_path, monkeypatch):
    owner["answers"] = False
    monkeypatch.setattr(index_lock, "holder", lambda root: {"pid": 4242, "since": 0})
    healthy, detail = mon.owner_probe(tmp_path)
    assert healthy and "indexing" in detail


def test_doctor_owner_exit_code(owner, tmp_path, capsys):
    args = argparse.Namespace(root=str(tmp_path), strict=False, owner=True)
    with pytest.raises(SystemExit) as ok:
        mon.cmd_doctor(args)
    assert ok.value.code == 0
    owner["answers"] = False
    with pytest.raises(SystemExit) as stuck:
        mon.cmd_doctor(args)
    assert stuck.value.code == 1
    assert "does not answer" in capsys.readouterr().out


def test_a_starting_owner_is_not_reported_stuck(owner, tmp_path, monkeypatch):
    # The pid file was just written: the owner is coming up and silent.
    pidfile = tmp_path / "owner.pid"
    pidfile.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(ipc, "owner_pidfile", lambda root: pidfile)
    owner["answers"] = False
    healthy, detail = mon.owner_probe(tmp_path)
    assert healthy and "starting" in detail

    # Past the startup window the same silence is a stuck owner.
    old = pidfile.stat().st_mtime - mon._OWNER_STARTUP_GRACE_S - 5
    os.utime(pidfile, (old, old))
    healthy, detail = mon.owner_probe(tmp_path)
    assert not healthy and "cgh stop" in detail


def test_a_missing_repo_directory_is_healthy(tmp_path):
    # A worktree deleted while still listed in a supervisor policy.
    healthy, detail = mon.owner_probe(tmp_path / "gone")
    assert healthy and "no owner" in detail
