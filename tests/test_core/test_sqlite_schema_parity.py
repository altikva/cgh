# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The SQLite graph backend (the standalone binary's) keeps the
#              same columns as DuckDB. md_section.kind was added to DuckDB
#              only, so indexing any markdown file crashed under SQLite.

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from codegraph.core.db_sqlite import SQLiteGraphDB


def _sqlite_columns(path: Path) -> dict[str, set[str]]:
    con = sqlite3.connect(path)
    try:
        tables = [
            r[0]
            for r in con.execute("select name from sqlite_master where type='table'")
        ]
        return {
            t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")} for t in tables
        }
    finally:
        con.close()


def test_sqlite_has_every_duckdb_column(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    from codegraph.core.schema_duckdb import init_schema

    dcon = duckdb.connect(str(tmp_path / "g.duckdb"))
    init_schema(dcon)
    duck = {
        t: {
            c
            for (c,) in dcon.execute(
                "select column_name from information_schema.columns where table_name = ?",
                [t],
            ).fetchall()
        }
        for (t,) in dcon.execute(
            "select table_name from information_schema.tables"
        ).fetchall()
    }
    dcon.close()

    SQLiteGraphDB(str(tmp_path / "g.sqlite"))
    lite = _sqlite_columns(tmp_path / "g.sqlite")
    missing = {t: sorted(cols - lite.get(t, set())) for t, cols in duck.items()}
    missing = {t: c for t, c in missing.items() if c}
    assert not missing, f"columns missing in the SQLite schema: {missing}"


def test_old_sqlite_graph_gains_the_kind_column(tmp_path: Path) -> None:
    db = tmp_path / "g.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE md_section (id TEXT PRIMARY KEY, title TEXT, level BIGINT, "
        "file_path TEXT, start_line BIGINT, end_line BIGINT, body_preview TEXT, "
        "anchor TEXT)"
    )
    con.commit()
    con.close()
    SQLiteGraphDB(str(db))
    assert "kind" in _sqlite_columns(db)["md_section"]


def test_sqlite_index_of_a_markdown_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Title\n\nSome text.\n\n## Part\n\nMore.\n")
    result = subprocess.run(
        [sys.executable, "-m", "codegraph", "index", "--root", str(repo)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CGH_DB": "sqlite"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "md_section has no column" not in result.stderr
    lite = sqlite3.connect(repo / ".codegraph" / "graph.sqlite")
    try:
        n = lite.execute("select count(*) from md_section").fetchone()[0]
    finally:
        lite.close()
    assert n >= 2
