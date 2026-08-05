# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh reset must remove the DuckDB graph (graph.duckdb), the
#              default backend since v0.4. A stale name filter only matched
#              the Kuzu graph.db, so reset left a corrupt DuckDB graph in
#              place and could not recover it.

from __future__ import annotations

import argparse

from codegraph.cli.commands_monitor import cmd_reset


def test_reset_removes_duckdb_graph_keeps_knowledge(tmp_path):
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    for name in ("graph.duckdb", "graph.duckdb.wal", "fts.db", "fts.db-wal"):
        (cg / name).write_bytes(b"x")
    # call_log.db holds knowledge / findings and must survive a reset.
    (cg / "call_log.db").write_bytes(b"keep")

    cmd_reset(
        argparse.Namespace(
            root=str(tmp_path), yes=True, no_reindex=True, drop_extra_dirs=False
        )
    )

    assert not (cg / "graph.duckdb").exists()
    assert not (cg / "graph.duckdb.wal").exists()
    assert not (cg / "fts.db").exists()
    assert not (cg / "fts.db-wal").exists()
    assert (cg / "call_log.db").read_bytes() == b"keep"
