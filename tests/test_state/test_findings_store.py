# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Finding store tests: replace semantics per scanner,
#              scanned-clean markers, blob-SHA dedup, filters, purge,
#              read-only opens for federation, and the end-to-end path
#              through index_file with a registered scanner plugin.

from __future__ import annotations

import subprocess

import pytest

import codegraph.plugins as plugins
from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    plugins._reset_for_tests()
    yield
    store.reset_for_tests()
    plugins._reset_for_tests()


def _f(key: str, value: str = "x", line: int = 0, severity: str = "info"):
    return ScanFinding(key=key, value=value, line=line, severity=severity)


class TestStore:
    def test_record_and_query(self, tmp_path):
        store.record_findings(
            tmp_path, "/repo/a.py", "pii-regex", [_f("pii.email", "2", 10, "warn")]
        )
        rows = store.query_findings(tmp_path)
        assert len(rows) == 1
        assert rows[0]["key"] == "pii.email"
        assert rows[0]["severity"] == "warn"
        assert rows[0]["line"] == 10

    def test_replace_semantics_per_scanner(self, tmp_path):
        store.record_findings(tmp_path, "/repo/a.py", "s1", [_f("pii.email")])
        store.record_findings(tmp_path, "/repo/a.py", "s2", [_f("secret.key")])
        store.record_findings(tmp_path, "/repo/a.py", "s1", [_f("pii.phone")])
        keys = sorted(r["key"] for r in store.query_findings(tmp_path))
        assert keys == ["pii.phone", "secret.key"]

    def test_scanned_clean_marker_and_dedup(self, tmp_path):
        store.record_findings(tmp_path, "/repo/a.py", "s1", [], blob_sha="abc")
        # No visible findings...
        assert store.query_findings(tmp_path) == []
        # ...but the scan itself is remembered.
        assert store.already_scanned(tmp_path, "/repo/a.py", "s1", "abc")
        assert not store.already_scanned(tmp_path, "/repo/a.py", "s1", "other")
        assert not store.already_scanned(tmp_path, "/repo/a.py", "s1", "")

    def test_filters(self, tmp_path):
        store.record_findings(
            tmp_path,
            "/repo/a.py",
            "s1",
            [_f("pii.email"), _f("secret.aws_key", severity="block")],
        )
        store.record_findings(tmp_path, "/repo/b.py", "s1", [_f("pii.phone")])

        assert len(store.query_findings(tmp_path, key_prefix="pii.")) == 2
        assert len(store.query_findings(tmp_path, severity="block")) == 1
        assert len(store.query_findings(tmp_path, file_path="/repo/b.py")) == 1
        assert (
            store.findings_for_file(tmp_path, "/repo/a.py", key_prefix="secret")[0][
                "key"
            ]
            == "secret.aws_key"
        )

    def test_purge_file(self, tmp_path):
        store.record_findings(tmp_path, "/repo/a.py", "s1", [_f("pii.email")])
        store.purge_file_findings(tmp_path, "/repo/a.py")
        assert store.query_findings(tmp_path) == []

    def test_readonly_open_for_federation(self, tmp_path):
        store.record_findings(tmp_path, "/repo/a.py", "s1", [_f("pii.email")])
        store.reset_for_tests()  # release the writer

        rows = store.query_findings_ro(store.findings_db_path(tmp_path))
        assert [r["key"] for r in rows] == ["pii.email"]
        assert store.query_findings_ro(tmp_path / "nope" / "findings.db") == []


class _FlagSecret:
    """Inline scanner: flags files whose text contains SECRET."""

    name = "flag-secret"
    deferred = False

    def scan(self, path, text, index):
        if "SECRET" in text:
            return [ScanFinding(key="secret.marker", value="found", severity="block")]
        return []


class TestScannerPipeline:
    def test_index_file_runs_inline_scanner(self, tmp_path, monkeypatch):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        clean = tmp_path / "clean.py"
        clean.write_text("def ok():\n    return 1\n", encoding="utf-8")
        dirty = tmp_path / "dirty.py"
        dirty.write_text("PASSWORD = 'SECRET'\n", encoding="utf-8")

        # Register the scanner the way a plugin would.
        plugins._registries.scanners.append(("test-plugin", _FlagSecret()))

        from codegraph.core.db import reset_connection
        from codegraph.indexer import index_repo

        reset_connection()
        try:
            index_repo(str(tmp_path))
        finally:
            reset_connection()

        rows = store.query_findings(tmp_path)
        assert len(rows) == 1
        assert rows[0]["key"] == "secret.marker"
        assert rows[0]["file"] == str(dirty)
        assert rows[0]["severity"] == "block"

        # The clean file was scanned too (marker row, no visible finding).
        assert store.findings_for_file(tmp_path, str(clean)) == []

        # Findings are searchable through the FTS.
        from codegraph.core.fts import fts_search, get_fts_conn

        hits = fts_search(get_fts_conn(tmp_path), "secret marker", limit=10)
        assert any(h.kind == "finding" for h in hits)

    def test_deferred_scanner_runs_off_hot_path(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nSECRET body\n", encoding="utf-8")

        class _Deferred(_FlagSecret):
            name = "flag-secret-deferred"
            deferred = True

        plugins._registries.scanners.append(("test-plugin", _Deferred()))

        from codegraph.core.db import reset_connection
        from codegraph.indexer import index_repo
        from codegraph.state.deferred_scan import drain_for_tests

        reset_connection()
        try:
            index_repo(str(tmp_path))
        finally:
            reset_connection()
        drain_for_tests()

        rows = store.query_findings(tmp_path, key_prefix="secret.")
        assert rows and rows[0]["scanner"] == "flag-secret-deferred"

    def test_raising_scanner_does_not_break_indexing(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        class _Boom:
            name = "boom"
            deferred = False

            def scan(self, path, text, index):
                raise RuntimeError("scanner bug")

        plugins._registries.scanners.append(("test-plugin", _Boom()))

        from codegraph.core.db import reset_connection
        from codegraph.indexer import index_repo

        reset_connection()
        try:
            stats = index_repo(str(tmp_path))
        finally:
            reset_connection()
        assert stats["errors"] == 0
