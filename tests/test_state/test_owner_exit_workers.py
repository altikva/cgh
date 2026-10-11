# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: An owner exiting (on an upgrade, say) keeps the markers of the
#              proxies still running, a proxy re-registers before it respawns
#              an owner, and an owner counts the live proxy that spawned it.
#              Before, the exiting owner deleted every marker and each owner
#              respawned afterwards stopped at 30s, cutting short its re-parse.

from __future__ import annotations

import os
import subprocess
import sys

from codegraph.state import ipc


def test_exit_keeps_live_markers_and_drops_the_rest(tmp_path):
    wd = ipc.workers_dir(tmp_path)
    wd.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (wd / str(os.getpid())).write_text("live\n")
    (wd / str(dead.pid)).write_text("dead\n")
    (wd / "keepalive").write_text("background\n")

    ipc.prune_workers_on_exit(tmp_path)

    assert sorted(p.name for p in wd.iterdir()) == [str(os.getpid())]
    assert ipc.live_workers(tmp_path) == [os.getpid()]


def test_exit_removes_an_emptied_workers_dir(tmp_path):
    wd = ipc.workers_dir(tmp_path)
    wd.mkdir(parents=True)
    (wd / "keepalive").write_text("background\n")

    ipc.prune_workers_on_exit(tmp_path)

    assert not wd.exists()


def test_recovery_registers_the_proxy_again(tmp_path, monkeypatch):
    seen: list[list[int]] = []
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)
    monkeypatch.setattr(ipc, "read_owner_pid", lambda root: None)
    monkeypatch.setattr(
        ipc,
        "spawn_owner",
        lambda root, watch, reindex: seen.append(ipc.live_workers(root)) or 4242,
    )

    assert ipc._recover_owner(tmp_path, watch=True) == 4242
    assert seen == [[os.getpid()]]


def test_spawner_alive_only_for_the_live_parent():
    assert ipc.spawner_alive(os.getppid())
    assert not ipc.spawner_alive(os.getpid())  # not our parent
    assert not ipc.spawner_alive(1)
    assert not ipc.spawner_alive(0)
