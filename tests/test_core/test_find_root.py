# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: find_codegraph_root walks up to the nearest .codegraph/, the way
#              git finds its repo root, so cgh works from any subdirectory.

from __future__ import annotations

from codegraph.core.config import find_codegraph_root


def test_finds_in_current_dir(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    assert find_codegraph_root(tmp_path) == tmp_path.resolve()


def test_finds_in_ancestor(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_codegraph_root(deep) == tmp_path.resolve()


def test_returns_none_when_absent(tmp_path):
    deep = tmp_path / "x" / "y"
    deep.mkdir(parents=True)
    assert find_codegraph_root(deep) is None


def test_nearest_root_wins(tmp_path):
    # A federated child has its own .codegraph/ inside the parent's. From the
    # child's subdir, the nearest (the child) must win, not the parent.
    (tmp_path / ".codegraph").mkdir()
    child = tmp_path / "child"
    child.mkdir()
    (child / ".codegraph").mkdir()
    sub = child / "sub"
    sub.mkdir()
    assert find_codegraph_root(sub) == child.resolve()
