# Copyright (c) 2026 ALTIKVA. All rights reserved.
# SPDX-License-Identifier: MIT AND CC-BY-NC-SA-4.0
#
# Project:     cgh (codegraph)
# Description: The deferred scan worker writes findings from its own thread
#              while the index loop writes from the main one. A second
#              SQLite connection there makes the loop's DELETE fail with
#              "database is locked" and aborts the whole scan.
# Author:      jndjama (Joy Ndjama)

from codegraph.state.deferred_scan import _feed_fts


class TestDeferredScanConnection:
    def test_reuses_the_cached_connection(self, monkeypatch):
        opened: list[str] = []
        used: list[object] = []
        cached = object()

        monkeypatch.setattr("codegraph.indexer._get_fts", lambda root: cached)
        monkeypatch.setattr(
            "codegraph.core.fts.get_fts_conn",
            lambda root=None: opened.append(str(root)) or object(),
        )
        monkeypatch.setattr(
            "codegraph.indexer._fts_ingest_findings",
            lambda conn, *a, **kw: used.append(conn),
        )
        monkeypatch.setattr("codegraph.core.fts.commit", lambda conn: None)

        _feed_fts("/tmp/repo", "/tmp/repo/a.py", "pii", [object()])

        assert used == [cached], "the findings must go through the cached connection"
        assert opened == [], "opening a second connection is what caused the lock"
