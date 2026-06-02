"""
Tests for `cgh migrate-to-duckdb`.

The migrate command re-indexes a repo currently on the Kuzu backend
into DuckDB, verifies counts, and optionally deletes graph.db. The
tests below drive cmd_migrate_to_duckdb directly with synthetic
argparse namespaces because subprocess-launching cgh inside pytest
would re-invoke the indexer in a separate process and lose coverage.
"""

from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path

import pytest

from codegraph.cli.commands_migrate import cmd_migrate_to_duckdb
from codegraph.core.db import reset_connection


def _seed_kuzu(repo_root: Path) -> None:
    """Run the indexer once on Kuzu so graph.db exists.

    The repo-default backend flipped from kuzu to duckdb in v0.5, so we
    must explicitly set CGH_DB=kuzu here — popping the env var would now
    seed a DuckDB DB and the migration would have nothing to convert.
    """
    os.environ["CGH_DB"] = "kuzu"
    reset_connection()
    from codegraph.indexer import index_repo

    try:
        index_repo(str(repo_root))
    finally:
        reset_connection()
        os.environ.pop("CGH_DB", None)


@pytest.fixture
def repo(tmp_path):
    """A tiny git-tracked Python repo, indexed once on Kuzu."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text(textwrap.dedent("""\
        def helper():
            return 1

        def caller():
            helper()
    """))
    (tmp_path / "b.py").write_text(textwrap.dedent("""\
        from .a import helper

        class Service:
            def run(self):
                helper()
    """))
    _seed_kuzu(tmp_path)
    yield tmp_path
    # Cleanup: drop the env var and reset cached conn between tests
    os.environ.pop("CGH_DB", None)
    reset_connection()


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(root=str(root), yes=False, keep_kuzu=False, force=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestSuccessfulMigration:
    def test_keep_kuzu_leaves_both_files(self, repo):
        cmd_migrate_to_duckdb(_args(repo, keep_kuzu=True))
        assert (repo / ".codegraph" / "graph.db").exists()
        assert (repo / ".codegraph" / "graph.duckdb").exists()

    def test_yes_deletes_kuzu(self, repo):
        cmd_migrate_to_duckdb(_args(repo, yes=True))
        assert not (repo / ".codegraph" / "graph.db").exists()
        assert (repo / ".codegraph" / "graph.duckdb").exists()


class TestPrechecks:
    def test_no_kuzu_skips(self, tmp_path, capsys):
        cg = tmp_path / ".codegraph"
        cg.mkdir()
        cmd_migrate_to_duckdb(_args(tmp_path))
        # Function should return early without raising; no DuckDB file written
        assert not (cg / "graph.duckdb").exists()

    def test_existing_duckdb_without_force_aborts(self, repo):
        # Pre-create graph.duckdb so migrate refuses
        (repo / ".codegraph" / "graph.duckdb").write_bytes(b"dummy")

        cmd_migrate_to_duckdb(_args(repo))
        # graph.db still there, graph.duckdb still the dummy bytes (untouched)
        assert (repo / ".codegraph" / "graph.duckdb").read_bytes() == b"dummy"

    def test_existing_duckdb_with_force_overwrites(self, repo):
        (repo / ".codegraph" / "graph.duckdb").write_bytes(b"dummy")
        cmd_migrate_to_duckdb(_args(repo, keep_kuzu=True, force=True))
        # No longer the dummy bytes — a real DuckDB file replaced it
        assert (repo / ".codegraph" / "graph.duckdb").read_bytes() != b"dummy"


class TestPostState:
    def test_migration_followed_by_status_works(self, repo):
        """After migrating + deleting graph.db, get_readonly_connection
        should auto-detect DuckDB and return a working conn."""
        cmd_migrate_to_duckdb(_args(repo, yes=True))

        os.environ.pop("CGH_DB", None)
        reset_connection()
        from codegraph.core.db import get_readonly_connection
        from codegraph.core.db_duckdb import DuckDBGraphDB

        conn = get_readonly_connection(repo)
        assert isinstance(conn, DuckDBGraphDB)
        assert conn.count_nodes("File") >= 2  # at least a.py + b.py
