# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the federated-children autostart that runs when a
#              parent owner comes up: autostart_children spawns an owner for
#              each initialized child whose owner is down and ties its life
#              to the parent via a pid worker marker; release_children drops
#              the markers on parent shutdown. Also pins the config flag
#              federate_auto_up (default true, disable per repo).

from __future__ import annotations

import os
from pathlib import Path

import pytest

import codegraph.state.ipc as ipc
from codegraph.analysis.federation import (
    add_subrepo,
    autostart_children,
    release_children,
)
from codegraph.core.config import load_config


def _mk_repo(root: Path, with_duckdb: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cg = root / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    if with_duckdb:
        (cg / "graph.duckdb").write_bytes(b"fake")
    return root


@pytest.fixture
def parent_and_child(tmp_path):
    parent = _mk_repo(tmp_path / "parent")
    child = _mk_repo(tmp_path / "child")
    add_subrepo(parent, child)
    return parent, child


class TestAutostartChildren:
    def test_starts_down_child_and_registers_pid_marker(
        self, parent_and_child, monkeypatch
    ):
        parent, child = parent_and_child
        spawned: list[tuple] = []

        def fake_spawn(root, watch, reindex):
            spawned.append((Path(root), watch, reindex))
            return 4321

        monkeypatch.setattr(ipc, "spawn_owner", fake_spawn)
        monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)

        results = autostart_children(parent)

        assert results == [
            {
                "child": str(child),
                "name": "child",
                "status": "started",
                "port": 4321,
            }
        ]
        assert spawned == [(child, True, False)]
        marker = child / ".codegraph" / "workers" / f"parent-{os.getpid()}"
        assert marker.exists()

    def test_release_children_drops_pid_marker(self, parent_and_child, monkeypatch):
        parent, child = parent_and_child
        monkeypatch.setattr(ipc, "spawn_owner", lambda root, watch, reindex: 4321)
        monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)
        autostart_children(parent)
        marker = child / ".codegraph" / "workers" / f"parent-{os.getpid()}"
        assert marker.exists()

        release_children(parent)

        assert not marker.exists()

    def test_child_already_up_is_untouched(self, parent_and_child, monkeypatch):
        parent, child = parent_and_child

        def boom(*a, **k):
            raise AssertionError("spawn_owner must not be called")

        monkeypatch.setattr(ipc, "spawn_owner", boom)
        monkeypatch.setattr(ipc, "is_owner_alive", lambda root: True)

        results = autostart_children(parent)

        assert results == [
            {"child": str(child), "name": "child", "status": "already-up"}
        ]
        # No pid marker: an already-up child must not be kept alive by this
        # parent, otherwise two repos federating each other never shut down.
        marker = child / ".codegraph" / "workers" / f"parent-{os.getpid()}"
        assert not marker.exists()

    def test_uninitialized_child_is_skipped(self, tmp_path, monkeypatch):
        parent = _mk_repo(tmp_path / "parent")
        child = tmp_path / "bare"
        child.mkdir()
        add_subrepo(parent, child)
        monkeypatch.setattr(
            ipc, "spawn_owner", lambda *a, **k: pytest.fail("must not spawn")
        )

        results = autostart_children(parent)

        assert results == [
            {
                "child": str(child),
                "name": "bare",
                "status": "skipped",
                "reason": "not initialized",
            }
        ]

    def test_failed_spawn_removes_pid_marker(self, parent_and_child, monkeypatch):
        parent, child = parent_and_child
        monkeypatch.setattr(ipc, "spawn_owner", lambda root, watch, reindex: None)
        monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)

        results = autostart_children(parent)

        assert results == [{"child": str(child), "name": "child", "status": "failed"}]
        marker = child / ".codegraph" / "workers" / f"parent-{os.getpid()}"
        assert not marker.exists()

    def test_no_children_is_a_noop(self, tmp_path):
        parent = _mk_repo(tmp_path / "solo")
        assert autostart_children(parent) == []
        release_children(parent)  # must not raise either


class TestFederateAutoUpConfig:
    def test_defaults_to_true(self, tmp_path):
        _mk_repo(tmp_path / "repo")
        assert load_config(tmp_path / "repo").federate_auto_up is True

    def test_can_be_disabled_in_config(self, tmp_path):
        repo = _mk_repo(tmp_path / "repo")
        (repo / ".codegraph" / "config.toml").write_text(
            "[codegraph]\nfederate_auto_up = false\n", encoding="utf-8"
        )
        assert load_config(repo).federate_auto_up is False
