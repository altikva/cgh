"""
Tests for cgh init's auto-migration hook.

When cmd_init runs in a repo where only graph.db exists (no graph.duckdb),
the new _auto_migrate_kuzu_to_duckdb helper transparently re-indexes
into DuckDB and deletes graph.db on a clean match.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from codegraph.cli.commands_init import _auto_migrate_kuzu_to_duckdb
from codegraph.core.db import reset_connection


def _seed_kuzu(repo_root: Path) -> None:
    os.environ["CGH_DB"] = "kuzu"
    reset_connection()
    from codegraph.indexer import index_repo

    try:
        index_repo(str(repo_root))
    finally:
        reset_connection()
        os.environ.pop("CGH_DB", None)


@pytest.fixture
def kuzu_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text(textwrap.dedent("""\
        def helper(): return 1
        def caller(): helper()
    """))
    _seed_kuzu(tmp_path)
    yield tmp_path
    os.environ.pop("CGH_DB", None)
    reset_connection()


class TestAutoMigrate:
    def test_migrates_existing_kuzu_to_duckdb(self, kuzu_repo):
        cg = kuzu_repo / ".codegraph"
        assert (cg / "graph.db").exists()
        assert not (cg / "graph.duckdb").exists()

        _auto_migrate_kuzu_to_duckdb(kuzu_repo)

        # graph.db is gone, graph.duckdb is there.
        assert not (cg / "graph.db").exists()
        assert (cg / "graph.duckdb").exists()

    def test_no_op_when_only_duckdb_present(self, tmp_path):
        cg = tmp_path / ".codegraph"
        cg.mkdir()
        (cg / "graph.duckdb").write_bytes(b"existing")

        _auto_migrate_kuzu_to_duckdb(tmp_path)
        # Untouched
        assert (cg / "graph.duckdb").read_bytes() == b"existing"

    def test_no_op_on_fresh_repo(self, tmp_path):
        """cgh init on a fresh repo has no .codegraph/ at all — helper just returns."""
        _auto_migrate_kuzu_to_duckdb(tmp_path)
        # No directory should have been created.
        assert not (tmp_path / ".codegraph").exists()

    def test_no_op_when_both_present(self, kuzu_repo):
        cg = kuzu_repo / ".codegraph"
        (cg / "graph.duckdb").write_bytes(b"existing-duck")

        _auto_migrate_kuzu_to_duckdb(kuzu_repo)
        # graph.db still there, graph.duckdb still our dummy bytes
        assert (cg / "graph.db").exists()
        assert (cg / "graph.duckdb").read_bytes() == b"existing-duck"
