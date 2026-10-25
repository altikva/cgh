# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: a DuckDB invalidation in the middle of a reindex. A fatal error
#              poisons the whole database instance: every later statement
#              fails until the connection is closed. The connection cache
#              must drop it and reopen, and a reindex whose writes did not all
#              land must rebuild from source (or fail) instead of reporting
#              success and marking the index fresh.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import codegraph.indexer as idx
from codegraph.core import db as dbmod
from codegraph.core.db import get_connection, reset_connection
from codegraph.state.scan_meta import read_meta

duckdb = pytest.importorskip("duckdb")

_INVALIDATED = (
    "FATAL Error: Failed: database has been invalidated because of a previous "
    "fatal error. The database must be restarted prior to being used again."
)
_CHECKPOINT_FAILED = (
    "FATAL Error: Failed to create checkpoint because of error: Could not "
    'write file "graph.duckdb.wal": File too large'
)


@pytest.fixture(autouse=True)
def _duckdb_backend(monkeypatch):
    monkeypatch.setenv("CGH_DB", "duckdb")
    idx._last_rebuild.clear()
    yield
    idx._last_rebuild.clear()


class _Poisoned:
    """Stands in for a duckdb connection once its database is invalidated:
    the first statement after ``fire()`` raises the original fatal error,
    every later one the "has been invalidated" error, as DuckDB does."""

    def __init__(self, real) -> None:
        self._real = real
        self.fired = False
        self.armed = False

    def fire(self) -> None:
        self.armed = True

    def execute(self, *args, **kwargs):
        if self.armed:
            msg = _INVALIDATED if self.fired else _CHECKPOINT_FAILED
            self.fired = True
            raise duckdb.FatalException(msg)
        return self._real.execute(*args, **kwargs)

    def close(self) -> None:
        self._real.close()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _repo(root: Path) -> Path:
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "from lib import helper\n\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    idx.index_repo(root)
    return root


def _edit_and_commit(root: Path) -> None:
    (root / "lib.py").write_text(
        "def helper():\n    return 2\n\n\ndef helper2():\n    return 3\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "from lib import helper, helper2\n\n\n"
        "def main():\n    return helper() + helper2()\n",
        encoding="utf-8",
    )
    _git(root, "commit", "-qam", "edit")


def _poison_on_first_ingest(monkeypatch, root: Path) -> _Poisoned:
    """Invalidate the cached connection when the reindex writes its first
    file's symbols: the File node and its purge have landed, the rest not."""
    conn = get_connection(root)
    poisoned = _Poisoned(conn._conn)
    conn._conn = poisoned
    real = idx._ingest_code

    def ingest(*args, **kwargs):
        poisoned.fire()
        return real(*args, **kwargs)

    monkeypatch.setattr(idx, "_ingest_code", ingest)
    return poisoned


def _graph(root: Path) -> dict:
    conn = get_connection(root)
    callees = conn.execute(
        "SELECT count(*) FROM edge_calls c JOIN function f ON f.id = c.from_id "
        "WHERE f.name = 'main'"
    ).get_next()[0]
    return {
        "helper2": bool(conn.find_node_keys("Function", "name", "helper2")),
        "main_calls": callees,
    }


# --- the connection cache -------------------------------------------------


def test_fatal_error_flags_the_connection(tmp_path):
    root = _repo(tmp_path)
    conn = get_connection(root)
    conn._conn = _Poisoned(conn._conn)
    conn._conn.fire()
    with pytest.raises(duckdb.FatalException):
        conn.count_nodes("File")
    assert conn.invalidated
    reset_connection(root)


def test_invalidated_connection_is_dropped_and_reopened(tmp_path):
    root = _repo(tmp_path)
    old = get_connection(root)
    old._conn = _Poisoned(old._conn)
    old._conn.fire()
    with pytest.raises(duckdb.FatalException):
        old.count_nodes("File")
    before = dbmod.invalidation_count(root)
    assert before >= 1

    # Neither cache may hand the poisoned object out again.
    fresh = get_connection(root)
    assert fresh is not old
    assert fresh.count_nodes("File") == 2
    assert dbmod.get_readonly_connection(root) is fresh
    assert dbmod.invalidation_count(root) == before  # monotonic over the drop
    reset_connection(root)


def test_invalidated_readonly_connection_is_dropped(tmp_path):
    root = _repo(tmp_path)
    reset_connection(root)
    ro = dbmod.get_readonly_connection(root)
    assert ro is not None
    ro._conn = _Poisoned(ro._conn)
    ro._conn.fire()
    with pytest.raises(duckdb.FatalException):
        ro.count_nodes("File")
    again = dbmod.get_readonly_connection(root)
    assert again is not None and again is not ro
    assert again.count_nodes("File") == 2
    reset_connection(root)


def test_reopen_that_stays_invalidated_fails_loudly(tmp_path, monkeypatch):
    from codegraph.core.db_duckdb import DuckDBGraphDB

    root = _repo(tmp_path)
    reset_connection(root)
    monkeypatch.setattr(DuckDBGraphDB, "healthy", lambda self: False)
    with pytest.raises(dbmod.GraphInvalidatedError, match="cgh stop"):
        get_connection(root)
    assert dbmod.get_readonly_connection(root) is None
    monkeypatch.undo()
    reset_connection(root)


def test_any_fatal_exception_counts_as_fatal():
    # A failed checkpoint invalidates the database just as surely as a
    # corrupt index, though its text names neither.
    assert idx.is_fatal_graph_error(duckdb.FatalException(_CHECKPOINT_FAILED))


# --- reindex never reports a lost write as a success -----------------------


def test_incremental_reindex_rebuilds_after_a_fatal_mid_run(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _edit_and_commit(root)
    poisoned = _poison_on_first_ingest(monkeypatch, root)

    result = idx.incremental_reindex(root)

    assert poisoned.fired
    assert result["mode"] == "rebuilt_after_fatal"
    assert "fatal DuckDB error" in result["recovered_from"]
    assert _graph(root) == {"helper2": True, "main_calls": 2}
    assert read_meta(root) is not None  # written by the rebuild, not before
    reset_connection(root)


def test_full_index_rebuilds_after_a_fatal_a_step_swallowed(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _edit_and_commit(root)
    real = idx._reparse_unstamped
    state = {"calls": 0}

    def swallowing(repo_root, activity_log):
        # A pass that catches and logs every error, as several do: the
        # fatal never reaches the index loop.
        state["calls"] += 1
        if state["calls"] == 1:
            conn = get_connection(repo_root)
            conn._conn = _Poisoned(conn._conn)
            conn._conn.fire()
            try:
                conn.unstamped_files()
            except Exception as exc:
                activity_log(repo_root, "scan_error", str(exc))
            return []
        return real(repo_root, activity_log)

    monkeypatch.setattr(idx, "_reparse_unstamped", swallowing)
    stats = idx.index_repo(root)

    assert state["calls"] == 2  # the run was redone on a fresh graph
    assert stats["errors"] == 0
    assert _graph(root) == {"helper2": True, "main_calls": 2}
    log = (root / ".codegraph" / "activity.log").read_text(encoding="utf-8")
    assert "graph_corrupt_recover" in log
    reset_connection(root)


def test_failed_rebuild_raises_and_leaves_the_index_not_fresh(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    assert read_meta(root) is not None

    def never_lands(repo_root, invalidations_before, stats):
        raise idx.GraphWriteError("graph sanity check failed")

    monkeypatch.setattr(idx, "_check_write_landed", never_lands)
    with pytest.raises(idx.GraphWriteError):
        idx.index_repo(root)
    assert read_meta(root) is None
    reset_connection(root)


def test_sanity_check_reports_half_written_files(tmp_path):
    root = _repo(tmp_path)
    conn = get_connection(root)
    conn.execute("DELETE FROM file_stamp WHERE path = ?", [str(root / "lib.py")])
    stats = {"indexed": 2, "errors": 0}
    idx._check_write_landed(root, dbmod.invalidation_count(root), stats)
    assert stats["incomplete_files"] == [str(root / "lib.py")]
    assert stats["errors"] == 1
    reset_connection(root)


def test_sanity_check_rejects_an_empty_graph(tmp_path):
    root = _repo(tmp_path)
    get_connection(root).execute("DELETE FROM file")
    with pytest.raises(idx.GraphWriteError, match="no File node"):
        idx._check_write_landed(
            root, dbmod.invalidation_count(root), {"indexed": 2, "errors": 0}
        )
    reset_connection(root)


# --- the real thing ---------------------------------------------------------

# Run in a child process: it lowers RLIMIT_FSIZE so a CHECKPOINT cannot write
# the database file, which is how DuckDB 1.x raises a genuine FatalException
# and invalidates the database instance.
_REAL_INVALIDATION = r"""
import json, resource, signal, sys
from pathlib import Path

import codegraph.indexer as idx
from codegraph.core.db import get_connection
from codegraph.state.scan_meta import read_meta

root = Path(sys.argv[1])
real = idx._ingest_code
fired = []
limits = resource.getrlimit(resource.RLIMIT_FSIZE)


def ingest(conn, *args, **kwargs):
    if not fired:
        fired.append(True)
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, limits[1]))
        try:
            conn.raw.execute("CHECKPOINT")
        except Exception as exc:
            fired.append(type(exc).__name__)
            raise
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, limits)
    return real(conn, *args, **kwargs)


idx._ingest_code = ingest
out = {}
try:
    result = idx.incremental_reindex(root)
    out["mode"] = result.get("mode")
except Exception as exc:
    out["raised"] = f"{type(exc).__name__}: {exc}"[:300]
idx._ingest_code = real
out["fired"] = fired
out["meta"] = read_meta(root) is not None
try:
    conn = get_connection(root)
    out["helper2"] = bool(conn.find_node_keys("Function", "name", "helper2"))
    out["main_calls"] = conn.execute(
        "SELECT count(*) FROM edge_calls c JOIN function f ON f.id = c.from_id "
        "WHERE f.name = 'main'"
    ).get_next()[0]
except Exception as exc:
    out["lookup_error"] = f"{type(exc).__name__}: {exc}"[:200]
print("RESULT " + json.dumps(out))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="needs RLIMIT_FSIZE")
def test_real_duckdb_invalidation_during_incremental_reindex(tmp_path):
    (tmp_path / "repo").mkdir()
    root = _repo(tmp_path / "repo")
    _edit_and_commit(root)
    reset_connection(root)  # the child process opens the graph itself
    script = tmp_path / "invalidate.py"
    script.write_text(_REAL_INVALIDATION, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    proc = subprocess.run(
        [sys.executable, str(script), str(root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, proc.stdout + proc.stderr
    out = json.loads(lines[-1][len("RESULT ") :])
    if out["fired"] != [True, "FatalException"]:
        pytest.skip(f"this platform did not invalidate the database: {out}")

    assert out.get("mode") == "rebuilt_after_fatal", out
    assert out["helper2"] is True
    assert out["main_calls"] == 2
    assert out["meta"] is True
