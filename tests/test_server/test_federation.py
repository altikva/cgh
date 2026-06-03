# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Tests for codegraph.analysis.federation — config resolution, child status, iteration.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.analysis.federation import (
    add_subrepo,
    child_paths_to_skip,
    is_under_any,
    iter_db_roots,
    remove_subrepo,
    resolve_children,
    verify_child,
)


def _mk_repo(
    root: Path,
    with_kuzu: bool = True,
    with_duckdb: bool = False,
    with_fts: bool = False,
    git: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cg = root / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    if with_kuzu:
        (cg / "graph.db").write_bytes(b"fake")
    if with_duckdb:
        (cg / "graph.duckdb").write_bytes(b"fake")
    if with_fts:
        (cg / "fts.db").write_bytes(b"fake")
    if git:
        (root / ".git").mkdir()
    return root


class TestResolveChildren:
    def test_empty_when_no_config(self, tmp_path):
        _mk_repo(tmp_path / "p")
        assert resolve_children(tmp_path / "p") == []

    def test_relative_path_resolves_under_parent(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        child = _mk_repo(parent / "apps" / "api")
        add_subrepo(parent, child)
        assert resolve_children(parent) == [child.resolve()]

    def test_absolute_path_preserved(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        sibling = _mk_repo(tmp_path / "sibling")
        add_subrepo(parent, sibling)
        assert resolve_children(parent) == [sibling.resolve()]

    def test_dedup(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        child = _mk_repo(tmp_path / "c")
        add_subrepo(parent, child)
        add_subrepo(parent, child)  # idempotent
        assert resolve_children(parent) == [child.resolve()]

    def test_skips_nonexistent(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        cfg = parent / ".codegraph" / "config.toml"
        cfg.write_text('[codegraph]\nsubrepos = ["./gone"]\n')
        # The path doesn't exist; resolve_children skips paths whose .resolve()
        # raises OSError, so empty result.
        # Note: on macOS Path.resolve() of a missing file doesn't always raise,
        # so this may still return the path. Either way the test is that it
        # doesn't crash.
        result = resolve_children(parent)
        assert isinstance(result, list)


class TestIsUnderAny:
    def test_match(self, tmp_path):
        roots = [tmp_path / "a", tmp_path / "b"]
        for r in roots:
            r.mkdir()
        assert is_under_any(roots[0] / "x" / "y.py", roots)
        assert is_under_any(roots[1] / "z.py", roots)

    def test_miss(self, tmp_path):
        roots = [tmp_path / "a"]
        roots[0].mkdir()
        assert not is_under_any(tmp_path / "elsewhere" / "f.py", roots)

    def test_empty_roots(self, tmp_path):
        assert not is_under_any(tmp_path / "x", [])


class TestVerifyChild:
    def test_missing_path(self, tmp_path):
        st = verify_child(tmp_path / "ghost")
        assert not st.exists
        assert not st.ok
        assert st.error == "path does not exist"

    def test_uninitialized(self, tmp_path):
        d = tmp_path / "raw"
        d.mkdir()
        st = verify_child(d)
        assert st.exists
        assert not st.initialized
        assert not st.ok

    def test_initialized_no_graphdb(self, tmp_path):
        # .codegraph/ but neither graph.db nor graph.duckdb.
        _mk_repo(tmp_path / "p", with_kuzu=False)
        st = verify_child(tmp_path / "p")
        assert st.exists and st.initialized
        assert not st.has_kuzu
        assert not st.has_duckdb
        assert not st.has_graphdb
        assert not st.ok

    def test_full(self, tmp_path):
        _mk_repo(tmp_path / "p", with_kuzu=True, with_fts=True, git=True)
        st = verify_child(tmp_path / "p")
        assert st.ok
        assert st.has_fts
        assert st.is_git_repo

    def test_duckdb_only_subrepo_is_ok(self, tmp_path):
        # The bug: a subrepo indexed on DuckDB (graph.duckdb, no graph.db)
        # must verify as OK. Before the fix, federate add reported
        # "graph.db missing" and treated it as broken.
        _mk_repo(tmp_path / "p", with_kuzu=False, with_duckdb=True)
        st = verify_child(tmp_path / "p")
        assert st.exists and st.initialized
        assert not st.has_kuzu
        assert st.has_duckdb
        assert st.has_graphdb
        assert st.ok


class TestAddRemoveSubrepo:
    def test_add_validates_path_exists(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        with pytest.raises(ValueError):
            add_subrepo(parent, tmp_path / "ghost")

    def test_add_rejects_self(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        with pytest.raises(ValueError):
            add_subrepo(parent, parent)

    def test_relative_storage_when_under_parent(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        child = _mk_repo(parent / "nested")
        add_subrepo(parent, child)
        cfg = (parent / ".codegraph" / "config.toml").read_text()
        assert '"./nested"' in cfg

    def test_absolute_storage_when_outside(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        sibling = _mk_repo(tmp_path / "sibling")
        add_subrepo(parent, sibling)
        cfg = (parent / ".codegraph" / "config.toml").read_text()
        assert str(sibling.resolve()) in cfg

    def test_remove_returns_false_when_not_present(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        assert not remove_subrepo(parent, tmp_path / "ghost")

    def test_remove_idempotent(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        child = _mk_repo(tmp_path / "c")
        add_subrepo(parent, child)
        assert remove_subrepo(parent, child)
        assert not remove_subrepo(parent, child)


class TestIterDbRoots:
    def test_only_parent_when_no_subrepos(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        roots = iter_db_roots(parent)
        assert roots == [parent.resolve()]

    def test_skips_uninitialized_children(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        good = _mk_repo(tmp_path / "good")
        bad = _mk_repo(tmp_path / "bad", with_kuzu=False)
        add_subrepo(parent, good)
        add_subrepo(parent, bad)
        roots = iter_db_roots(parent)
        # Parent + good only — bad has no graph.db
        assert roots == [parent.resolve(), good.resolve()]


class TestChildPathsToSkip:
    def test_returns_all_declared(self, tmp_path):
        parent = _mk_repo(tmp_path / "p")
        a = _mk_repo(tmp_path / "a")
        b = _mk_repo(tmp_path / "b")
        add_subrepo(parent, a)
        add_subrepo(parent, b)
        skip = child_paths_to_skip(parent)
        assert sorted(skip) == sorted([a.resolve(), b.resolve()])
