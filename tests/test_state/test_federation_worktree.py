# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for worktree-aware federation.

from __future__ import annotations

import shutil
import subprocess

import pytest


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )


def _mkrepo(p):
    p.mkdir(parents=True)
    _git(p, "init", "-q")
    _git(p, "config", "user.email", "a@b.c")
    _git(p, "config", "user.name", "t")
    (p / "f.txt").write_text("x\n")
    _git(p, "add", ".")
    _git(p, "commit", "-qm", "i")


@pytest.fixture
def layout(tmp_path):
    api_main = tmp_path / "ws" / "api"
    front_main = tmp_path / "ws" / "front"
    _mkrepo(api_main)
    _mkrepo(front_main)
    api_wt = tmp_path / "wt" / "c" / "api"
    front_wt = tmp_path / "wt" / "c" / "front"
    _git(api_main, "worktree", "add", "-q", str(api_wt))
    _git(front_main, "worktree", "add", "-q", str(front_wt))
    (front_wt / ".codegraph").mkdir()
    return {
        "api_main": api_main,
        "front_main": front_main,
        "api_wt": api_wt,
        "front_wt": front_wt,
    }


def test_prefers_sibling_worktree(layout):
    from codegraph.integrations.worktree import worktree_sibling

    assert (
        worktree_sibling(layout["api_wt"], layout["front_main"])
        == layout["front_wt"].resolve()
    )


def test_main_checkout_no_redirect(layout):
    from codegraph.integrations.worktree import worktree_sibling

    assert worktree_sibling(layout["api_main"], layout["front_main"]) is None


def test_sibling_without_codegraph_ignored(layout):
    from codegraph.integrations.worktree import worktree_sibling

    shutil.rmtree(layout["front_wt"] / ".codegraph")
    assert worktree_sibling(layout["api_wt"], layout["front_main"]) is None


def test_resolve_children_uses_sibling(layout):
    cfgdir = layout["api_wt"] / ".codegraph"
    cfgdir.mkdir(exist_ok=True)
    front = str(layout["front_main"])
    (cfgdir / "config.toml").write_text(f'[codegraph]\nsubrepos = ["{front}"]\n')
    from codegraph.analysis.federation import resolve_children

    children = resolve_children(layout["api_wt"])
    assert layout["front_wt"].resolve() in children
    assert layout["front_main"].resolve() not in children


def test_configured_child_worktree_is_kept(tmp_path):
    """A child configured as a linked worktree (a seed checkout) is never
    swapped for a ticket worktree that happens to sit beside the parent."""
    from codegraph.integrations.worktree import worktree_sibling

    api_main = tmp_path / "ws" / "api"
    front_main = tmp_path / "ws" / "front"
    _mkrepo(api_main)
    _mkrepo(front_main)
    seed_front = tmp_path / "seed" / "front"
    _git(front_main, "worktree", "add", "-q", "--detach", str(seed_front))
    api_ticket = tmp_path / "t" / "api-461"
    front_ticket = tmp_path / "t" / "front-371"
    _git(api_main, "worktree", "add", "-q", str(api_ticket))
    _git(front_main, "worktree", "add", "-q", str(front_ticket))
    (front_ticket / ".codegraph").mkdir()

    assert worktree_sibling(api_ticket, seed_front) is None


def test_flat_layout_with_several_siblings_keeps_configured_path(tmp_path):
    """Every ticket worktree in one directory: two front tickets sit beside
    an api ticket, so neither is picked."""
    from codegraph.analysis.federation import resolve_children
    from codegraph.integrations.worktree import worktree_sibling

    api_main = tmp_path / "ws" / "api"
    front_main = tmp_path / "ws" / "front"
    _mkrepo(api_main)
    _mkrepo(front_main)
    api_ticket = tmp_path / "t" / "api-461"
    _git(api_main, "worktree", "add", "-q", str(api_ticket))
    for name in ("front-371", "front-375"):
        wt = tmp_path / "t" / name
        _git(front_main, "worktree", "add", "-q", str(wt))
        (wt / ".codegraph").mkdir()

    assert worktree_sibling(api_ticket, front_main) is None
    cfg = api_ticket / ".codegraph"
    cfg.mkdir()
    (cfg / "config.toml").write_text(f'[codegraph]\nsubrepos = ["{front_main}"]\n')
    assert resolve_children(api_ticket) == [front_main.resolve()]
