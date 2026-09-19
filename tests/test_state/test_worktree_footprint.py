# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for worktree footprint hiding (in_git_worktree, hide_footprint).

from __future__ import annotations

import subprocess

import pytest


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo_and_worktree(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "config", "user.email", "a@b.c")
    _git(main, "config", "user.name", "t")
    (main / "CLAUDE.md").write_text("original\n")
    _git(main, "add", "CLAUDE.md")
    _git(main, "commit", "-qm", "init")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    return (main, wt)


def test_worktree_is_detected(repo_and_worktree):
    from codegraph.integrations.worktree import in_git_worktree

    main, wt = repo_and_worktree
    assert in_git_worktree(main) is False
    assert in_git_worktree(wt) is True


def test_hide_footprint_cleans_the_worktree(repo_and_worktree):
    from codegraph.integrations.worktree import hide_footprint

    _main, wt = repo_and_worktree
    (wt / "CLAUDE.md").write_text("original\n\nblock\n")
    skill = wt / ".claude" / "skills" / "cgh-x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("s\n")
    hidden = hide_footprint(wt)
    assert "CLAUDE.md" in hidden
    assert ".claude/skills/cgh-x" in hidden
    assert _git(wt, "ls-files", "-v", "CLAUDE.md").stdout.startswith("S")
    assert _git(wt, "status", "--porcelain").stdout.strip() == ""


def test_disabled_is_a_noop(repo_and_worktree):
    from codegraph.integrations.worktree import hide_footprint

    _main, wt = repo_and_worktree
    (wt / "CLAUDE.md").write_text("original\n\nx\n")
    hidden = hide_footprint(wt, enabled=False)
    assert hidden == []
    assert _git(wt, "status", "--porcelain", "CLAUDE.md").stdout.strip() != ""


def test_main_checkout_is_left_alone(repo_and_worktree):
    from codegraph.integrations.worktree import hide_footprint

    main, _wt = repo_and_worktree
    (main / "CLAUDE.md").write_text("original\n\ny\n")
    hidden = hide_footprint(main)
    assert hidden == []
