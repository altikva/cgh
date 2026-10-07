# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: index_repo self-heals a corrupt DuckDB graph. When indexing
#              hits the DuckDB "Failed to delete all rows from index" fatal
#              (a corrupt ART index left by an earlier crash), it rebuilds
#              the graph from scratch and retries instead of crashing.

from __future__ import annotations

import pytest

import codegraph.indexer as idx
from codegraph.core.db import get_db_path, reset_connection

_CORRUPT = (
    "FATAL Error: Invalid Input Error: Failed to delete all rows from index. "
    "Only deleted 0 out of 1 rows."
)


def test_is_graph_corrupt_matches_duckdb_fatal():
    assert idx._is_graph_corrupt(RuntimeError(_CORRUPT))
    assert idx._is_graph_corrupt(RuntimeError("database has been invalidated"))
    assert not idx._is_graph_corrupt(RuntimeError("some unrelated ValueError"))


def test_index_repo_self_heals_corrupt_graph(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    # First index builds a healthy graph.
    idx.index_repo(tmp_path, method="os_walk")
    reset_connection(tmp_path)
    assert get_db_path(tmp_path).exists()

    # Make the first index_file of the next run raise the DuckDB corruption
    # fatal, exactly as the reported crash did.
    real_index_file = idx.index_file
    state = {"raised": False}

    def flaky(*args, **kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError(_CORRUPT)
        return real_index_file(*args, **kwargs)

    monkeypatch.setattr(idx, "index_file", flaky)

    # Must recover (wipe + rebuild the graph, retry) instead of propagating.
    stats = idx.index_repo(tmp_path, method="os_walk")
    assert state["raised"] is True  # it did hit the corruption
    assert stats["indexed"] >= 1  # and rebuilt the graph on retry
    reset_connection(tmp_path)
    assert get_db_path(tmp_path).exists()


@pytest.fixture(autouse=True)
def _no_rebuild_cooldown():
    idx._last_rebuild.clear()
    yield
    idx._last_rebuild.clear()


def _fatal() -> Exception:
    import duckdb

    return duckdb.InvalidInputException(_CORRUPT)


def _invalidated() -> Exception:
    import duckdb

    return duckdb.FatalException(
        "FATAL Error: Failed: database has been invalidated because of a "
        "previous fatal error. The database must be restarted prior to being "
        "used again."
    )


def _healthy_repo(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    idx.index_repo(tmp_path, method="os_walk")
    return tmp_path


def _has_foo(root) -> bool:
    from codegraph.core.db import get_connection

    return bool(get_connection(root).find_node_keys("Function", "name", "foo"))


def test_fatal_check_accepts_only_duckdb_errors_that_start_with_the_report():
    import duckdb

    assert idx.is_fatal_graph_error(_fatal())
    assert idx.is_fatal_graph_error(_invalidated())
    # Wrapped by another layer: still found through the cause chain.
    try:
        try:
            raise _invalidated()
        except Exception as inner:
            raise RuntimeError("tool failed") from inner
    except RuntimeError as wrapped:
        assert idx.is_fatal_graph_error(wrapped)

    # The same words from anything that is not DuckDB must not count: a file
    # or a tool argument named after the sentence would otherwise wipe the
    # graph.
    assert not idx.is_fatal_graph_error(RuntimeError(_CORRUPT))
    assert not idx.is_fatal_graph_error(
        OSError("cannot read 'database has been invalidated because.py'")
    )
    # A DuckDB error that only quotes the sentence further in.
    assert not idx.is_fatal_graph_error(
        duckdb.CatalogException(
            "Catalog Error: Table with name 'Failed to delete all rows from "
            "index.' does not exist"
        )
    )


def test_rebuild_ignores_other_errors(tmp_path):
    root = _healthy_repo(tmp_path)
    assert idx.rebuild_corrupt_graph(root, ValueError("unrelated")) is None
    assert idx.rebuild_corrupt_graph(root, RuntimeError(_CORRUPT)) is None
    assert _has_foo(root)
    reset_connection(root)


def test_rebuild_wipes_and_reindexes(tmp_path, monkeypatch):
    from codegraph.state import scan_meta

    root = _healthy_repo(tmp_path)
    seen = {}
    real = idx._index_repo

    def spy(*args, **kwargs):
        # At rebuild time the old scan must be forgotten: the index is not
        # fresh while the graph is empty.
        seen["meta_during_rebuild"] = scan_meta.read_meta(root)
        return real(*args, **kwargs)

    monkeypatch.setattr(idx, "_index_repo", spy)
    stats = idx.rebuild_corrupt_graph(root, _fatal())
    assert stats is not None and stats["indexed"] >= 1
    assert seen["meta_during_rebuild"] is None
    assert scan_meta.read_meta(root) is not None  # written again by the rebuild
    assert _has_foo(root)
    reset_connection(root)


def test_rebuild_does_not_loop(tmp_path):
    root = _healthy_repo(tmp_path)
    assert idx.rebuild_corrupt_graph(root, _fatal()) is not None
    # The same error again right away: no second wipe.
    assert idx.rebuild_corrupt_graph(root, _fatal()) is None
    assert _has_foo(root)
    reset_connection(root)


def test_watcher_rebuilds_after_a_fatal_reindex(tmp_path, monkeypatch):
    import codegraph.state.watcher as watcher

    root = _healthy_repo(tmp_path)

    def poisoned(*args, **kwargs):
        raise _fatal()

    monkeypatch.setattr(watcher, "index_file", poisoned)
    handler = watcher._CodeGraphHandler(root)
    handler._reindex(str(root / "a.py"))  # must not raise
    assert _has_foo(root)
    log = (root / ".codegraph" / "activity.log").read_text(encoding="utf-8")
    assert "graph_corrupt_recover" in log
    reset_connection(root)


def test_tool_call_on_corrupt_graph_starts_a_rebuild(tmp_path, monkeypatch):
    import codegraph.server as srv

    root = _healthy_repo(tmp_path)
    monkeypatch.setattr(srv, "_root", root)

    @srv._logged_tool
    def broken_tool() -> str:
        raise _invalidated()

    with pytest.raises(RuntimeError, match="being rebuilt from source"):
        broken_tool()
    srv._rebuild_thread.join(timeout=30)
    assert not srv._rebuild_thread.is_alive()
    assert _has_foo(root)
    log = (root / ".codegraph" / "activity.log").read_text(encoding="utf-8")
    assert "graph_corrupt_recover" in log

    # An error that only repeats the words is passed through untouched.
    @srv._logged_tool
    def echoing_tool() -> str:
        raise ValueError("no symbol named 'database has been invalidated'")

    with pytest.raises(ValueError, match="no symbol named"):
        echoing_tool()
    reset_connection(root)
