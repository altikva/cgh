# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Seeding one checkout's store from another: the prefix rewrite that
#              moves stored paths and the symbol and edge ids embedding them to
#              the new root, on both graph backends, plus what must survive it
#              untouched (blob shas, line numbers, unresolved external targets)
#              and what must not travel at all (the per-checkout auth key).

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codegraph.state.relocate import RelocateError, relocate_store


def _sqlite_source(src: Path) -> None:
    """A minimal sqlite-backend store holding source-root-absolute values."""
    cg = src / ".codegraph"
    cg.mkdir(parents=True)
    con = sqlite3.connect(str(cg / "graph.sqlite"))
    con.execute("CREATE TABLE file (path TEXT PRIMARY KEY, git_blob_sha TEXT)")
    con.execute("CREATE TABLE calls (from_path TEXT, to_id TEXT)")
    con.execute("INSERT INTO file VALUES (?, ?)", (f"{src}/pkg/mod.py", "ab" * 20))
    con.execute(
        "INSERT INTO calls VALUES (?, ?)",
        (f"{src}/pkg/mod.py", f"{src}/pkg/mod.py::fn"),
    )
    # An unresolved external target carries no root prefix and must stay as is.
    con.execute(
        "INSERT INTO calls VALUES (?, ?)", (f"{src}/pkg/mod.py", "os.path::join")
    )
    con.commit()
    con.close()
    (cg / "auth.key").write_text("per-checkout secret")
    (cg / "scan_meta.json").write_text(
        json.dumps({"root": str(src), "git_head": "abc123", "git_branch": "develop"})
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    # The indexer stores resolved absolute paths, so a fixture must resolve too:
    # on macOS /var is a symlink to /private/var and the prefixes would not match.
    base = tmp_path.resolve()
    src, dst = base / "main", base / "wt"
    (dst / ".codegraph").mkdir(parents=True)
    return src, dst


def test_relocate_rewrites_paths_and_embedded_ids(tmp_path):
    src, dst = _roots(tmp_path)
    _sqlite_source(src)

    result = relocate_store(src, dst)

    assert result["backend"] == "sqlite"
    con = sqlite3.connect(str(dst / ".codegraph" / "graph.sqlite"))
    paths = [r[0] for r in con.execute("SELECT path FROM file")]
    calls = con.execute("SELECT from_path, to_id FROM calls").fetchall()
    con.close()

    assert paths == [f"{dst}/pkg/mod.py"]
    # Only the leading root moves: the "::fn" the id embeds is still there.
    assert (f"{dst}/pkg/mod.py", f"{dst}/pkg/mod.py::fn") in calls
    assert not any(str(src) in str(row) for row in calls)


def test_relocate_preserves_blob_shas(tmp_path):
    # Blob shas are content hashes, independent of the root. Keeping them is what
    # lets the follow-up incremental pass recognise unchanged files and skip them.
    src, dst = _roots(tmp_path)
    _sqlite_source(src)

    relocate_store(src, dst)

    con = sqlite3.connect(str(dst / ".codegraph" / "graph.sqlite"))
    shas = [r[0] for r in con.execute("SELECT git_blob_sha FROM file")]
    con.close()
    assert shas == ["ab" * 20]


def test_relocate_leaves_unresolved_targets_alone(tmp_path):
    src, dst = _roots(tmp_path)
    _sqlite_source(src)

    relocate_store(src, dst)

    con = sqlite3.connect(str(dst / ".codegraph" / "graph.sqlite"))
    calls = con.execute("SELECT from_path, to_id FROM calls").fetchall()
    con.close()
    assert (f"{dst}/pkg/mod.py", "os.path::join") in calls


def test_relocate_stamps_scan_meta_root(tmp_path):
    # incremental_reindex rebuilds from scratch when the recorded root differs
    # from the live one, so the target root must be stamped for the copied paths
    # to be trusted; the source's git head rides along until the next pass.
    src, dst = _roots(tmp_path)
    _sqlite_source(src)

    relocate_store(src, dst)

    meta = json.loads((dst / ".codegraph" / "scan_meta.json").read_text())
    assert meta["root"] == str(dst.resolve())
    assert meta["git_head"] == "abc123"


def test_relocate_does_not_copy_the_auth_key(tmp_path):
    # Each checkout mints its own; a shared key would let one speak for the other.
    src, dst = _roots(tmp_path)
    _sqlite_source(src)

    relocate_store(src, dst)

    assert not (dst / ".codegraph" / "auth.key").exists()


def test_relocate_without_a_graph_raises(tmp_path):
    src, dst = _roots(tmp_path)
    (src / ".codegraph").mkdir(parents=True)

    with pytest.raises(RelocateError):
        relocate_store(src, dst)


def test_relocate_rewrites_the_duckdb_backend(tmp_path):
    # The pip default. Its columns are enumerated from information_schema, a
    # different path from sqlite's, so it gets its own coverage.
    duckdb = pytest.importorskip("duckdb")
    src, dst = _roots(tmp_path)
    (src / ".codegraph").mkdir(parents=True)

    con = duckdb.connect(str(src / ".codegraph" / "graph.duckdb"))
    con.execute("CREATE TABLE file (path VARCHAR PRIMARY KEY, git_blob_sha VARCHAR)")
    con.execute(
        "CREATE TABLE function (id VARCHAR, file_path VARCHAR, start_line INTEGER)"
    )
    con.execute("CREATE TABLE calls (from_id VARCHAR, to_id VARCHAR)")
    con.execute("INSERT INTO file VALUES (?, ?)", [f"{src}/pkg/mod.py", "cd" * 20])
    con.execute(
        "INSERT INTO function VALUES (?, ?, ?)",
        [f"{src}/pkg/mod.py::fn", f"{src}/pkg/mod.py", 12],
    )
    con.execute(
        "INSERT INTO calls VALUES (?, ?)",
        [f"{src}/pkg/mod.py::fn", f"{src}/pkg/other.py::helper"],
    )
    con.execute(
        "INSERT INTO calls VALUES (?, ?)", [f"{src}/pkg/mod.py::fn", "json::loads"]
    )
    con.commit()
    con.close()

    result = relocate_store(src, dst)
    assert result["backend"] == "duckdb"

    con = duckdb.connect(str(dst / ".codegraph" / "graph.duckdb"))
    fns = con.execute("SELECT id, file_path, start_line FROM function").fetchall()
    calls = con.execute("SELECT from_id, to_id FROM calls").fetchall()
    shas = [r[0] for r in con.execute("SELECT git_blob_sha FROM file").fetchall()]
    con.close()

    # Both the id and the path move; the line number is not a path and is intact.
    assert fns == [(f"{dst}/pkg/mod.py::fn", f"{dst}/pkg/mod.py", 12)]
    assert (f"{dst}/pkg/mod.py::fn", f"{dst}/pkg/other.py::helper") in calls
    assert (f"{dst}/pkg/mod.py::fn", "json::loads") in calls
    assert shas == ["cd" * 20]


def _wal_fts(path: Path, rows: int) -> sqlite3.Connection:
    """An fts.db in WAL mode whose committed rows stay in the -wal file: the
    connection is left open with autocheckpoint off, as a live reader would."""
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE symbols (sym_id TEXT, file_path TEXT)")
    con.executemany(
        "INSERT INTO symbols VALUES (?, ?)",
        [
            (f"{path.parent.parent}/m{i}.py::f", f"{path.parent.parent}/m{i}.py")
            for i in range(rows)
        ],
    )
    con.commit()
    return con


def test_relocate_carries_fts_rows_still_in_the_wal(tmp_path):
    # A byte copy of fts.db alone drops every page still in fts.db-wal and leaves
    # a torn file ("database disk image is malformed" at the next init step).
    src, dst = _roots(tmp_path)
    _sqlite_source(src)
    live = _wal_fts(src / ".codegraph" / "fts.db", 500)
    try:
        assert (src / ".codegraph" / "fts.db-wal").stat().st_size > 0
        relocate_store(src, dst)
    finally:
        live.close()

    con = sqlite3.connect(str(dst / ".codegraph" / "fts.db"))
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    paths = [r[0] for r in con.execute("SELECT DISTINCT file_path FROM symbols")]
    con.close()
    assert len(paths) == 500
    assert all(p.startswith(f"{dst}/") for p in paths)


def test_relocate_drops_a_stale_target_wal(tmp_path):
    # A WAL left by the target's previous store would be replayed over the copy.
    src, dst = _roots(tmp_path)
    _sqlite_source(src)
    _wal_fts(src / ".codegraph" / "fts.db", 10).close()
    stale = _wal_fts(dst / ".codegraph" / "fts.db", 300)
    try:
        leftover = (dst / ".codegraph" / "fts.db-wal").read_bytes()
    finally:
        stale.close()
    (dst / ".codegraph" / "fts.db-wal").write_bytes(leftover)

    relocate_store(src, dst)

    con = sqlite3.connect(str(dst / ".codegraph" / "fts.db"))
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT count(*) FROM symbols").fetchone()[0] == 10
    con.close()


def test_relocate_over_a_target_graph_this_process_holds_open(tmp_path):
    # init probes the existing store with a cached read-only connection before
    # seeding. Re-running --from on an initialised checkout then tripped DuckDB's
    # "same database file with a different configuration" on the rewrite.
    duckdb = pytest.importorskip("duckdb")
    from codegraph.core.db import get_readonly_connection, reset_connection

    src, dst = _roots(tmp_path)
    (src / ".codegraph").mkdir(parents=True)
    for root in (src, dst):
        con = duckdb.connect(str(root / ".codegraph" / "graph.duckdb"))
        con.execute("CREATE TABLE file (path VARCHAR, git_blob_sha VARCHAR)")
        con.execute("INSERT INTO file VALUES (?, ?)", [f"{root}/a.py", "ef" * 20])
        con.commit()
        con.close()

    try:
        assert get_readonly_connection(dst) is not None
        relocate_store(src, dst)
    finally:
        reset_connection(dst)

    con = duckdb.connect(str(dst / ".codegraph" / "graph.duckdb"))
    assert con.execute("SELECT path FROM file").fetchall() == [(f"{dst}/a.py",)]
    con.close()
