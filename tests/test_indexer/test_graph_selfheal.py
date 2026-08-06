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
