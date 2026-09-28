# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: One index run per repo at a time. Two full indexes of the same
#              worktree used to start seconds apart (init, then the owner's
#              startup reindex) and ran side by side. Covers the lock across
#              processes and threads, stale-lock takeover, nesting (an
#              incremental pass falling back to a full index), index_repo
#              refusing a busy repo, and scan_status reporting a running index.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

import pytest

from codegraph.state.index_lock import IndexBusy, holder, index_lock


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


@pytest.fixture
def other_process():
    """A live process that is not this one, to stand in for another indexer."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc.pid
    proc.kill()
    proc.wait()


def _claim(root, pid: int) -> None:
    (root / ".codegraph" / "index.lock").write_text(f"{pid}\n", encoding="utf-8")


def test_a_live_index_in_another_process_blocks_a_second(root, other_process):
    _claim(root, other_process)
    with pytest.raises(IndexBusy) as exc, index_lock(root):
        pass
    assert exc.value.pid == other_process


def test_a_lock_left_by_a_dead_process_is_taken_over(root, other_process):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _claim(root, dead.pid)
    with index_lock(root):
        assert holder(root)["pid"] == os.getpid()
    assert holder(root) is None


def test_another_thread_of_this_process_is_refused(root):
    inside, release = threading.Event(), threading.Event()

    def hold():
        with index_lock(root):
            inside.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert inside.wait(10)
    try:
        with pytest.raises(IndexBusy), index_lock(root):
            pass
    finally:
        release.set()
        t.join()


def test_nested_runs_in_one_thread_share_the_lock(root):
    with index_lock(root):
        with index_lock(root):
            assert holder(root)["pid"] == os.getpid()
        # The inner exit must not release what the outer run still holds.
        assert holder(root)["pid"] == os.getpid()
    assert not (root / ".codegraph" / "index.lock").exists()


def _git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        check=True,
    )  # fmt: skip
    (tmp_path / ".codegraph").mkdir(exist_ok=True)
    return tmp_path


def test_index_repo_refuses_a_repo_being_indexed(tmp_path, other_process):
    from codegraph.indexer import index_repo

    repo = _git_repo(tmp_path)
    _claim(repo, other_process)
    with pytest.raises(IndexBusy):
        index_repo(repo)


def test_incremental_falling_back_to_full_keeps_its_own_lock(tmp_path):
    # No index yet: the incremental pass falls back to a full index_repo, which
    # takes the lock again on the same thread. It must nest, not refuse itself.
    from codegraph.indexer import incremental_reindex

    repo = _git_repo(tmp_path)
    result = incremental_reindex(repo)
    assert result["mode"] == "fallback_full"
    assert holder(repo) is None


def test_scan_status_reports_a_running_index(root):
    from codegraph.state.scan_meta import scan_status

    assert scan_status(root)["indexing"] is None
    with index_lock(root):
        running = scan_status(root)["indexing"]
        assert running["pid"] == os.getpid() and running["since"]
    assert scan_status(root)["indexing"] is None
