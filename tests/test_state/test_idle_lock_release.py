# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Idle write-lock release. A watcher burst in an owner with
#              no MCP proxy attached must drop the cached write
#              connection so a federated parent's read-only fan-out can
#              open the child's DB again; parent-<pid> markers keep the
#              child alive but never block the release; a live MCP
#              worker does block it.

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

import codegraph.state.watcher as watcher_mod
from codegraph.state.ipc import (
    live_workers,
    mcp_workers,
    register_parent_marker,
    register_worker,
    unregister_parent_marker,
)
from codegraph.state.watcher import _CodeGraphHandler


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(watcher_mod, "_GIT_IGNORE_CACHE", {})
    monkeypatch.setattr(watcher_mod, "_GIT_IGNORE_CACHE_TS", time.time())
    return tmp_path


class TestWorkerAccounting:
    def test_parent_marker_keeps_alive_but_is_not_an_mcp_worker(self, tmp_path):
        register_parent_marker(tmp_path)
        assert live_workers(tmp_path) == [os.getpid()]
        assert mcp_workers(tmp_path) == []
        unregister_parent_marker(tmp_path)
        assert live_workers(tmp_path) == []

    def test_plain_worker_counts_in_both(self, tmp_path):
        register_worker(tmp_path)
        assert os.getpid() in live_workers(tmp_path)
        assert os.getpid() in mcp_workers(tmp_path)
        (tmp_path / ".codegraph" / "workers" / str(os.getpid())).unlink()

    def test_dead_parent_marker_is_pruned(self, tmp_path):
        wd = tmp_path / ".codegraph" / "workers"
        wd.mkdir(parents=True)
        (wd / "parent-999999999").write_text("999999999\n", encoding="utf-8")
        assert live_workers(tmp_path) == []
        assert not (wd / "parent-999999999").exists()


class TestIdleRelease:
    def _flush_one(self, repo: Path, monkeypatch) -> list[str]:
        """Run one watcher burst over a.py, recording reset calls."""
        released: list[str] = []
        monkeypatch.setattr(
            "codegraph.core.db.reset_connection", lambda: released.append("reset")
        )
        monkeypatch.setattr(watcher_mod, "index_file", lambda p, root: True)
        handler = _CodeGraphHandler(repo)
        handler._schedule(str(repo / "a.py"))
        time.sleep(watcher_mod._DEBOUNCE + 0.4)
        return released

    def test_release_when_no_mcp_worker(self, repo, monkeypatch):
        register_parent_marker(repo)  # keeps the child alive, not an MCP worker
        try:
            assert self._flush_one(repo, monkeypatch) == ["reset"]
        finally:
            unregister_parent_marker(repo)

    def test_no_release_while_an_mcp_worker_is_attached(self, repo, monkeypatch):
        register_worker(repo)
        try:
            assert self._flush_one(repo, monkeypatch) == []
        finally:
            (repo / ".codegraph" / "workers" / str(os.getpid())).unlink()


class TestFanOutRecovers:
    def test_parent_ro_open_works_after_release(self, repo):
        """In-process rehearsal of the real sequence: the child's write
        conn blocks a read-only open of the same DuckDB file; releasing
        it (what the idle watcher now does) unblocks the fan-out."""
        from codegraph.analysis.federation import open_graphdb_ro
        from codegraph.core.db import get_connection, reset_connection

        reset_connection()
        conn = get_connection(repo)  # child owner's write conn, lock held
        conn.upsert_node("File", "path", str(repo / "a.py"), {"lang": "python"})
        with open_graphdb_ro(repo) as ro:
            assert ro is None  # locked: exactly the landing-zone failure

        reset_connection()  # the idle release
        with open_graphdb_ro(repo) as ro:
            assert ro is not None
            assert ro.count_nodes("File") == 1
        reset_connection()
