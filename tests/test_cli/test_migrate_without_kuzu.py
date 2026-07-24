# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Migration on a kuzu-less install. When the kuzu package is
#              absent (optional extra since 0.4.2, no cp3.14 wheels), the
#              old graph.db cannot be read, every Kuzu count is 0, and the
#              verifier used to flag the fresh DuckDB index as "unexplained
#              gains" forever, even with --force. do_migrate_to_duckdb must
#              instead swap with the explicit kuzu_unreadable status.
#              The kuzu availability probe is monkeypatched so the tests
#              run the same on installs with and without the extra.

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

from codegraph.cli import commands_migrate
from codegraph.cli.commands_migrate import do_migrate_to_duckdb
from codegraph.core.db import reset_connection


@pytest.fixture
def kuzu_repo_without_kuzu(tmp_path, monkeypatch):
    """A git repo carrying an unreadable graph.db, on an install where the
    kuzu package is (simulated as) missing."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text(
        textwrap.dedent("""\
        def helper():
            return 1

        def caller():
            helper()
    """)
    )
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    # Contents never get read: the migration must not even try to open it.
    (cg / "graph.db").write_bytes(b"unreadable kuzu bytes")
    monkeypatch.setattr(commands_migrate, "_kuzu_package_available", lambda: False)
    reset_connection()
    yield tmp_path
    os.environ.pop("CGH_DB", None)
    reset_connection()


class TestMigrateWithoutKuzuPackage:
    def test_swaps_and_deletes_kuzu(self, kuzu_repo_without_kuzu):
        repo = kuzu_repo_without_kuzu

        result = do_migrate_to_duckdb(repo, delete_kuzu=True)

        assert result.status == "kuzu_unreadable", result.message
        assert result.kuzu_deleted is True
        assert not (repo / ".codegraph" / "graph.db").exists()
        assert (repo / ".codegraph" / "graph.duckdb").exists()
        assert result.duckdb_nodes > 0

    def test_keep_kuzu_leaves_old_file(self, kuzu_repo_without_kuzu):
        repo = kuzu_repo_without_kuzu

        result = do_migrate_to_duckdb(repo, delete_kuzu=False)

        assert result.status == "kuzu_unreadable"
        assert result.kuzu_deleted is False
        assert (repo / ".codegraph" / "graph.db").exists()
        assert (repo / ".codegraph" / "graph.duckdb").exists()

    def test_duckdb_is_queryable_after_swap(self, kuzu_repo_without_kuzu):
        repo = kuzu_repo_without_kuzu
        do_migrate_to_duckdb(repo, delete_kuzu=True)

        os.environ.pop("CGH_DB", None)
        reset_connection()
        from codegraph.core.db import get_readonly_connection
        from codegraph.core.db_duckdb import DuckDBGraphDB

        conn = get_readonly_connection(repo)
        assert isinstance(conn, DuckDBGraphDB)
        assert conn.count_nodes("File") >= 1
