# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for analysis.churn. Builds a tiny real git repo in
#              tmp_path with deterministic author identities (passed via
#              `git -c`, never the global config) and asserts file_churn
#              reports the right per-file commit counts and authors and that
#              file_ownership returns the committing author. Also checks the
#              graceful empty result on a non-git directory.

from __future__ import annotations

import shutil
import subprocess

import pytest

from codegraph.analysis import churn

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(root, *args, author_name="Alice", author_email="alice@example.com"):
    """Run git in `root` with a pinned identity, ignoring any global config
    so the test is deterministic on every machine / CI runner."""
    subprocess.run(
        [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
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
def git_repo(tmp_path):
    """A tiny repo: commit 1 adds a.py + b.py (Alice), commit 2 edits a.py
    (Bob). So a.py has 2 commits / 2 authors, b.py has 1 commit / 1 author."""
    churn.clear_cache()
    _git(tmp_path, "init", "-q")

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py", "b.py")
    _git(tmp_path, "commit", "-q", "-m", "first")

    (tmp_path / "a.py").write_text("x = 1\nx2 = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")
    _git(
        tmp_path,
        "commit",
        "-q",
        "-m",
        "second",
        author_name="Bob",
        author_email="bob@example.com",
    )

    yield tmp_path
    churn.clear_cache()


def test_file_churn_commit_counts(git_repo):
    data = churn.file_churn(git_repo)
    assert set(data) == {"a.py", "b.py"}
    assert data["a.py"]["commits"] == 2
    assert data["b.py"]["commits"] == 1


def test_file_churn_authors(git_repo):
    data = churn.file_churn(git_repo)
    # a.py touched by Alice (first) and Bob (second).
    assert data["a.py"]["authors"] == {"Alice": 1, "Bob": 1}
    # b.py only by Alice.
    assert data["b.py"]["authors"] == {"Alice": 1}


def test_file_churn_lines_and_recency(git_repo):
    data = churn.file_churn(git_repo)
    # a.py: first commit adds 1 line, second adds 1 line -> 2 added total.
    assert data["a.py"]["lines_added"] >= 2
    assert data["a.py"]["last_modified"] > 0
    # a.py's last_modified is at least b.py's (a.py was edited later).
    assert data["a.py"]["last_modified"] >= data["b.py"]["last_modified"]


def test_file_ownership_returns_authors(git_repo):
    owners = churn.file_ownership(git_repo, "a.py")
    names = {o["name"] for o in owners}
    assert names == {"Alice", "Bob"}
    for o in owners:
        assert o["commits"] >= 1
        assert o["last_commit"] > 0


def test_file_ownership_single_author(git_repo):
    owners = churn.file_ownership(git_repo, "b.py")
    assert len(owners) == 1
    assert owners[0]["name"] == "Alice"
    assert owners[0]["commits"] == 1


def test_file_ownership_accepts_absolute_path(git_repo):
    owners = churn.file_ownership(git_repo, str(git_repo / "b.py"))
    assert [o["name"] for o in owners] == ["Alice"]


def test_non_git_dir_returns_empty(tmp_path):
    churn.clear_cache()
    assert churn.file_churn(tmp_path) == {}
    assert churn.file_ownership(tmp_path, "whatever.py") == []


def test_churn_cache_is_used(git_repo, monkeypatch):
    # First call populates the cache; a second call must not shell out again.
    churn.clear_cache()
    first = churn.file_churn(git_repo)

    def _boom(*a, **k):
        raise AssertionError("git should not be called on a cache hit")

    monkeypatch.setattr(churn, "_git", _boom)
    # _head_sha also uses _git, so patch it to return the same head.
    monkeypatch.setattr(churn, "_head_sha", lambda r: "cachekeyhead")
    # Re-seed cache under the patched head so the lookup hits.
    churn._CHURN_CACHE[(str(git_repo.resolve()), "cachekeyhead", None)] = first
    second = churn.file_churn(git_repo)
    assert second == first
