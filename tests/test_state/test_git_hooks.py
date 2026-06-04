# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the git reindex hooks (install / uninstall / status,
#              chaining onto existing hooks, the shared-hooksPath guard).

from __future__ import annotations

import stat
import subprocess

import pytest

from codegraph.state.git_hooks import (
    HOOK_EVENTS,
    git_hooks_status,
    hooks_target_info,
    install_git_hooks,
    uninstall_git_hooks,
)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init")
    # Pin a local hooks dir so a developer's global core.hooksPath doesn't
    # leak into the test.
    _git(tmp_path, "config", "core.hooksPath", ".git/hooks")
    return tmp_path


class TestInstall:
    def test_installs_all_events(self, repo):
        written = install_git_hooks(repo)
        assert set(written) == set(HOOK_EVENTS)
        for event in HOOK_EVENTS:
            hook = repo / ".git" / "hooks" / event
            assert hook.exists()
            assert "cgh _reindex_hook" in hook.read_text()
            # executable bit set
            assert hook.stat().st_mode & stat.S_IXUSR

    def test_status_reflects_install(self, repo):
        assert git_hooks_status(repo) == {e: False for e in HOOK_EVENTS}
        install_git_hooks(repo)
        assert git_hooks_status(repo) == {e: True for e in HOOK_EVENTS}

    def test_post_checkout_guards_on_branch_flag(self, repo):
        install_git_hooks(repo)
        body = (repo / ".git" / "hooks" / "post-checkout").read_text()
        # Only reindex on a branch switch ($3 == 1), not a file checkout.
        assert '[ "$3" = "1" ] || exit 0' in body

    def test_idempotent(self, repo):
        install_git_hooks(repo)
        first = (repo / ".git" / "hooks" / "post-merge").read_text()
        install_git_hooks(repo)
        second = (repo / ".git" / "hooks" / "post-merge").read_text()
        assert first == second
        # exactly one cgh block, not stacked
        assert second.count("# >>> cgh reindex >>>") == 1


class TestChaining:
    def test_preserves_existing_hook(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "post-merge").write_text('#!/bin/sh\necho "mine"\n')
        install_git_hooks(repo)
        body = (hooks / "post-merge").read_text()
        assert 'echo "mine"' in body
        assert "cgh _reindex_hook" in body

    def test_uninstall_restores_existing_hook(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "post-merge").write_text('#!/bin/sh\necho "mine"\n')
        install_git_hooks(repo)
        uninstall_git_hooks(repo)
        body = (hooks / "post-merge").read_text()
        assert 'echo "mine"' in body
        assert "cgh reindex" not in body

    def test_uninstall_deletes_cgh_only_hook(self, repo):
        install_git_hooks(repo)
        uninstall_git_hooks(repo)
        # A hook cgh created from scratch (shebang + block only) is removed.
        assert not (repo / ".git" / "hooks" / "post-merge").exists()
        assert git_hooks_status(repo) == {e: False for e in HOOK_EVENTS}


class TestSharedHooksPath:
    def test_shared_path_is_flagged(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init")
        shared = tmp_path / "global-hooks"
        shared.mkdir()
        _git(repo, "config", "core.hooksPath", str(shared))

        target, is_shared = hooks_target_info(repo)
        assert is_shared is True
        assert target == shared

    def test_local_path_not_flagged(self, repo):
        target, is_shared = hooks_target_info(repo)
        assert is_shared is False


class TestNonGitRepo:
    def test_returns_empty_outside_git(self, tmp_path):
        assert install_git_hooks(tmp_path) == []
        target, is_shared = hooks_target_info(tmp_path)
        assert target is None
