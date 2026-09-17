# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The fresh-repo backend default adapts to the install: DuckDB when
#              its native library is importable (pip/uvx), SQLite otherwise (the
#              standalone binary that ships without DuckDB). CGH_DB and on-disk
#              detection still win over the default.

from __future__ import annotations

import pytest

import codegraph.core.db as db


def test_duckdb_available_true_in_dev_env():
    # The test env installs duckdb as a core dep.
    assert db.duckdb_available() is True


def test_fresh_default_is_duckdb_when_available(monkeypatch):
    monkeypatch.delenv("CGH_DB", raising=False)
    monkeypatch.setattr(db, "duckdb_available", lambda: True)
    assert db._backend(None) == "duckdb"


def test_fresh_default_falls_back_to_sqlite_without_duckdb(monkeypatch):
    # This is the standalone-binary case: no DuckDB bundled -> SQLite default.
    monkeypatch.delenv("CGH_DB", raising=False)
    monkeypatch.setattr(db, "duckdb_available", lambda: False)
    assert db._backend(None) == "sqlite"


def test_env_var_overrides_the_adaptive_default(monkeypatch):
    monkeypatch.setenv("CGH_DB", "sqlite")
    monkeypatch.setattr(db, "duckdb_available", lambda: True)
    assert db._backend(None) == "sqlite"


def test_on_disk_file_wins_over_default(monkeypatch, tmp_path):
    # An existing graph.duckdb keeps a repo on DuckDB even if the default would
    # be SQLite (binary opening a repo indexed by a pip install).
    monkeypatch.delenv("CGH_DB", raising=False)
    monkeypatch.setattr(db, "duckdb_available", lambda: False)
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    (cg / "graph.duckdb").write_bytes(b"")
    assert db._backend(tmp_path) == "duckdb"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
