# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: One DuckDBGraphDB shared by several threads, as in an owner: a
#              query must get its own result. duckdb keeps a query's columns
#              and rows on the connection, so without serialization another
#              thread's query can land between execute() and the fetch. In the
#              wild that hit about 1 call in 800; the connection here pauses
#              right after execute() to widen that window until an unguarded
#              backend fails nearly every run.

from __future__ import annotations

import threading
import time

from codegraph.core.db_duckdb import DuckDBGraphDB


class _SlowConn:
    """A real duckdb connection that pauses just after execute(), the point
    where an unguarded caller is exposed. execute() returns the proxy, as duckdb
    returns the connection, so description and fetchall() read shared state."""

    def __init__(self, real) -> None:
        self._real = real

    def execute(self, *args, **kwargs):
        self._real.execute(*args, **kwargs)
        time.sleep(0.002)
        return self

    def __getattr__(self, name):
        return getattr(self._real, name)


def _graph(tmp_path) -> DuckDBGraphDB:
    db = DuckDBGraphDB(str(tmp_path / "graph.duckdb"))
    for fid, name in (("/a.py::caller", "caller"), ("/a.py::callee", "callee")):
        db.upsert_node("Function", "id", fid, {"name": name, "file_path": "/a.py"})
    db.ensure_edge("CALLS", "/a.py::caller", "/a.py::callee", {})
    db._conn = _SlowConn(db._conn)
    return db


def test_concurrent_neighbor_queries_keep_their_own_columns(tmp_path):
    db = _graph(tmp_path)
    wrong: list[set[str]] = []

    def lookup(expected: str, **query) -> None:
        for _ in range(40):
            for row in db.find_neighbors("CALLS", **query):
                if set(row) != {expected}:
                    wrong.append(set(row))

    # The two lookups context_for_task makes for one function: who calls it
    # (src_name) and what it calls (dst_name).
    threads = [
        threading.Thread(
            target=lookup,
            args=("src_name",),
            kwargs={"dst_where": {"name": "callee"}, "return_src": ["name"]},
        ),
        threading.Thread(
            target=lookup,
            args=("dst_name",),
            kwargs={"src_where": {"name": "caller"}, "return_dst": ["name"]},
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert wrong == []


def test_locked_methods_can_call_each_other(tmp_path):
    # Public methods call other public methods; the lock must be reentrant or
    # the first such call deadlocks. Run on a worker thread with a timeout so a
    # regression fails instead of hanging the suite.
    db = _graph(tmp_path)
    done = threading.Event()

    def work() -> None:
        db.purge_file_data("/a.py")
        db.count_nodes("Function")
        done.set()

    threading.Thread(target=work, daemon=True).start()
    assert done.wait(10), "graph method deadlocked calling another graph method"
