# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Test that the parent indexer skips files inside federated subrepos.

from __future__ import annotations

from codegraph.indexer import _discover_os_walk, _walk_include_dirs


class TestOsWalkSubrepoSkip:
    def test_skips_subrepo_tree(self, tmp_path):
        # Parent has app.py + a subrepo with secret.py.
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "app.py").write_text("def parent_only(): pass\n")
        (parent / ".codegraph").mkdir()

        sub = parent / "apps" / "api"
        sub.mkdir(parents=True)
        (sub / "secret.py").write_text("def child_only(): pass\n")
        (sub / ".codegraph").mkdir()
        (sub / ".codegraph" / "graph.db").write_bytes(b"fake")

        # Federate the subrepo
        cfg = parent / ".codegraph" / "config.toml"
        cfg.write_text('[codegraph]\nsubrepos = ["./apps/api"]\n')

        files = _discover_os_walk(parent)
        names = {f.name for f in files}
        assert "app.py" in names
        assert "secret.py" not in names

    def test_no_skip_when_no_subrepos(self, tmp_path):
        # Without federation, both files should be discovered.
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".codegraph").mkdir()
        (parent / "a.py").write_text("# a")
        (parent / "b.py").write_text("# b")

        files = _discover_os_walk(parent)
        names = {f.name for f in files}
        assert names == {"a.py", "b.py"}


class TestIncludeDirsSubrepoSkip:
    def test_include_dirs_skip_overlapping_subrepo(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".codegraph").mkdir()

        # An "include_dir" that physically contains a subrepo
        inc = parent / "shared"
        inc.mkdir()
        (inc / "util.py").write_text("# util")
        (inc / "subrepo").mkdir()
        (inc / "subrepo" / ".codegraph").mkdir()
        (inc / "subrepo" / ".codegraph" / "graph.db").write_bytes(b"fake")
        (inc / "subrepo" / "private.py").write_text("# private")

        cfg = parent / ".codegraph" / "config.toml"
        cfg.write_text(
            '[codegraph]\ninclude_dirs = ["./shared"]\nsubrepos = ["./shared/subrepo"]\n'
        )

        files = _walk_include_dirs(parent)
        names = {f.name for f in files}
        assert "util.py" in names
        assert "private.py" not in names
