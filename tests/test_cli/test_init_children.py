# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Init propagation to federated children: a parent init
#              brings uninitialized subrepos to a working index, a
#              federation cycle cannot loop (children run with
#              --no-children), and one failing child never aborts the
#              others.

from __future__ import annotations

import subprocess

import pytest

from codegraph.analysis.federation import add_subrepo, verify_child
from codegraph.cli.commands_init import _init_children


@pytest.fixture
def parent_with_children(tmp_path):
    parent = tmp_path / "parent"
    (parent / ".codegraph").mkdir(parents=True)
    bare = tmp_path / "bare-child"
    bare.mkdir()
    (bare / "lib.py").write_text("def child_fn():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
    subprocess.run(["git", "add", "-A"], cwd=bare, check=True)
    add_subrepo(parent, bare)
    return parent, bare


class TestInitChildren:
    def test_bare_child_gets_initialized_and_indexed(self, parent_with_children):
        parent, bare = parent_with_children
        assert not verify_child(bare).ok

        _init_children(parent, assume_yes=True)

        status = verify_child(bare)
        assert status.initialized
        assert status.has_graphdb

    def test_cycle_cannot_loop(self, tmp_path):
        """A federates B and B federates A: the child subprocess runs with
        --no-children, so propagation stops after one level and this test
        terminates."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        (a / ".codegraph").mkdir(parents=True)
        b.mkdir()
        (b / "x.py").write_text("y = 1\n", encoding="utf-8")
        add_subrepo(a, b)

        _init_children(a, assume_yes=True)

        assert verify_child(b).initialized
        # Declare the cycle after B exists, then re-propagate from A.
        add_subrepo(b, a)
        _init_children(a, assume_yes=True)  # must terminate, not recurse

    def test_one_failing_child_does_not_abort_the_rest(self, tmp_path, monkeypatch):
        parent = tmp_path / "parent"
        (parent / ".codegraph").mkdir(parents=True)
        first = tmp_path / "first"
        second = tmp_path / "second"
        for child in (first, second):
            child.mkdir()
            (child / "m.py").write_text("z = 1\n", encoding="utf-8")
        add_subrepo(parent, first)
        add_subrepo(parent, second)

        real_run = subprocess.run
        calls = {"n": 0}

        def flaky_run(cmd, *args, **kwargs):
            if "-m" in cmd and "codegraph" in cmd:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("spawn failed")
            return real_run(cmd, *args, **kwargs)

        # _init_children imports subprocess at call time, so patching the
        # module global reaches it.
        monkeypatch.setattr("subprocess.run", flaky_run)

        _init_children(parent, assume_yes=True)

        assert calls["n"] == 2  # second child still attempted
        assert verify_child(second).initialized
