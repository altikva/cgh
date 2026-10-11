# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A file an agent just created, not yet added to git, is seen by
#              incremental reindex (indexed, and kept when the watcher already
#              indexed it) and by pattern_search's git grep backend. Ignored
#              files stay out of both.

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codegraph.analysis import pattern as P
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import incremental_reindex, index_file, index_repo


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    monkeypatch.setenv("CGH_DB", request.param)
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    yield request.param
    reset_connection()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    root = tmp_path.resolve()
    (root / "pkg").mkdir()
    (root / "pkg" / "base.py").write_text("def committed_fn():\n    return 1\n")
    (root / ".gitignore").write_text("ignored/\n.codegraph/\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(
        root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"
    )
    return root


def _symbols(root: Path) -> set[str]:
    conn = get_connection(root)
    return {name for (name,) in conn.list_node_fields("Function", ["name"])}


def test_incremental_indexes_a_new_untracked_file(repo, backend):
    index_repo(repo)
    (repo / "pkg" / "fresh.py").write_text("def brand_new_fn():\n    return 2\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "skip.py").write_text("def ignored_fn():\n    return 3\n")

    incremental_reindex(repo)

    names = _symbols(repo)
    assert "brand_new_fn" in names
    assert "committed_fn" in names
    assert "ignored_fn" not in names


def test_incremental_keeps_a_file_the_watcher_indexed(repo, backend):
    index_repo(repo)
    fresh = repo / "pkg" / "fresh.py"
    fresh.write_text("def watched_fn():\n    return 2\n")
    index_file(fresh, repo)  # what the watcher does on save
    assert "watched_fn" in _symbols(repo)

    incremental_reindex(repo)

    assert "watched_fn" in _symbols(repo)


def test_git_grep_sees_untracked_but_not_ignored(repo, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(
        P.shutil, "which", lambda name, *a, **k: None if name == "rg" else real(name)
    )
    (repo / "pkg" / "fresh.py").write_text("needle_xyz = 1\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "skip.py").write_text("needle_xyz = 2\n")

    hits, used = P.pattern_search(repo, "needle_xyz")

    assert used == "git-grep"
    assert [Path(h.file).name for h in hits] == ["fresh.py"]
