# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Watcher batching and Windows console suppression. A burst
#              of file events must produce ONE git check-ignore call for
#              the whole batch (the Windows conhost storm came from one
#              git.exe per file), gitignored files must not be indexed,
#              and quiet_subprocess_kwargs must emit CREATE_NO_WINDOW
#              creationflags on Windows and nothing elsewhere.

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

import codegraph.state.watcher as watcher_mod
from codegraph.core.utils import quiet_subprocess_kwargs
from codegraph.state.watcher import _CodeGraphHandler


class TestQuietSubprocessKwargs:
    def test_noop_on_posix(self):
        assert quiet_subprocess_kwargs() == {}

    def test_creationflags_on_windows(self, monkeypatch):
        monkeypatch.setattr("os.name", "nt")
        kwargs = quiet_subprocess_kwargs()
        assert "creationflags" in kwargs


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("generated.py\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "generated.py").write_text("y = 2\n", encoding="utf-8")
    # Fresh git-ignore cache per test.
    monkeypatch.setattr(watcher_mod, "_GIT_IGNORE_CACHE", {})
    monkeypatch.setattr(watcher_mod, "_GIT_IGNORE_CACHE_TS", time.time())
    return tmp_path


class TestBatchedIgnoreResolution:
    def test_one_git_call_per_burst(self, git_repo, monkeypatch):
        calls: list[list[str]] = []
        real_run = subprocess.run

        def counting_run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "check-ignore"]:
                calls.append(cmd)
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(watcher_mod.subprocess, "run", counting_run)
        indexed: list[str] = []
        monkeypatch.setattr(
            watcher_mod, "index_file", lambda p, root: indexed.append(str(p)) or True
        )

        handler = _CodeGraphHandler(git_repo)
        handler._schedule(str(git_repo / "tracked.py"))
        handler._schedule(str(git_repo / "generated.py"))
        time.sleep(watcher_mod._DEBOUNCE + 0.5)

        assert len(calls) == 1  # one batched call for the whole burst
        assert "--stdin" in calls[0]
        assert [Path(p).name for p in indexed] == ["tracked.py"]

    def test_cache_prevents_repeat_git_calls(self, git_repo, monkeypatch):
        calls: list[list[str]] = []
        real_run = subprocess.run

        def counting_run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "check-ignore"]:
                calls.append(cmd)
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(watcher_mod.subprocess, "run", counting_run)
        monkeypatch.setattr(watcher_mod, "index_file", lambda p, root: True)

        handler = _CodeGraphHandler(git_repo)
        for _ in range(3):
            handler._schedule(str(git_repo / "tracked.py"))
            time.sleep(watcher_mod._DEBOUNCE + 0.4)

        assert len(calls) == 1  # later bursts answered from the cache

    def test_cheap_ignores_never_reach_git(self, git_repo, monkeypatch):
        def forbidden_run(*args, **kwargs):
            raise AssertionError("git must not be called for cheap ignores")

        monkeypatch.setattr(watcher_mod.subprocess, "run", forbidden_run)
        handler = _CodeGraphHandler(git_repo)

        handler._schedule(str(git_repo / "image.png"))  # unsupported ext
        handler._schedule(str(git_repo / "node_modules" / "x.py"))  # ignore dir
        handler._schedule(str(git_repo / ".hidden" / "y.py"))  # dot dir
        time.sleep(watcher_mod._DEBOUNCE + 0.4)
        # No exception raised = git was never consulted.

    def test_git_failure_fails_open(self, git_repo, monkeypatch):
        def broken_run(cmd, *args, **kwargs):
            raise FileNotFoundError("git missing")

        monkeypatch.setattr(watcher_mod.subprocess, "run", broken_run)
        indexed: list[str] = []
        monkeypatch.setattr(
            watcher_mod, "index_file", lambda p, root: indexed.append(str(p)) or True
        )

        handler = _CodeGraphHandler(git_repo)
        handler._schedule(str(git_repo / "tracked.py"))
        time.sleep(watcher_mod._DEBOUNCE + 0.4)

        # Indexing an ignored file once beats dropping a real one.
        assert [Path(p).name for p in indexed] == ["tracked.py"]
