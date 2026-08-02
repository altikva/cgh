"""
Tests for the kuzu-optional behaviour added in v0.4.2.

Kuzu is now an optional dependency. ``codegraph.core.db`` and friends
must import without ``kuzu`` installed; only callers that actually need
the Kuzu backend should see the import (and the friendly error message
when it's missing).
"""

from __future__ import annotations

import builtins
import sys

import pytest

from codegraph.core.db import (
    _KUZU_MISSING_MSG,
    KuzuNotInstalled,
    _import_kuzu,
    get_connection,
    reset_connection,
)


def _without_kuzu(monkeypatch):
    """Force `import kuzu` to fail, even if it's actually installed."""
    monkeypatch.delitem(sys.modules, "kuzu", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "kuzu" or name.startswith("kuzu."):
            raise ImportError("No module named 'kuzu' (faked)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestLazyImport:
    def test_db_module_imports_without_kuzu(self, monkeypatch):
        """A clean reimport of codegraph.core.db must not require kuzu."""
        _without_kuzu(monkeypatch)
        # Drop all cgh modules so the reimport runs the top-level code again.
        for mod in list(sys.modules):
            if mod.startswith("codegraph"):
                monkeypatch.delitem(sys.modules, mod, raising=False)
        # Should not raise.
        import codegraph.core.db  # noqa: F401

    def test_import_kuzu_raises_helpful_error(self, monkeypatch):
        _without_kuzu(monkeypatch)
        with pytest.raises(KuzuNotInstalled) as exc:
            _import_kuzu()
        assert "pip install cgh[kuzu]" in str(exc.value)
        assert "cgh migrate-to-duckdb" in str(exc.value)
        assert str(exc.value) == _KUZU_MISSING_MSG
        # Stays a RuntimeError subclass so existing `except RuntimeError`
        # call sites keep catching it.
        assert isinstance(exc.value, RuntimeError)

    def test_missing_message_has_no_unreachable_docs_ref(self):
        # The message must not point pip/uv users at docs/ (not shipped
        # in the wheel) and must not carry an em or en dash.
        assert "docs/CONFIGURATION.md" not in _KUZU_MISSING_MSG
        assert "—" not in _KUZU_MISSING_MSG
        assert "–" not in _KUZU_MISSING_MSG


class TestBackendSelection:
    def test_duckdb_default_works_without_kuzu(self, tmp_path, monkeypatch):
        """A fresh repo with no env var defaults to DuckDB and must not
        try to import kuzu anywhere along the way."""
        _without_kuzu(monkeypatch)
        monkeypatch.delenv("CGH_DB", raising=False)
        reset_connection()
        conn = get_connection(tmp_path)
        # Smoke test: schema is up, count works.
        assert conn.count_nodes("File") == 0
        reset_connection()

    def test_kuzu_backend_raises_when_extra_missing(self, tmp_path, monkeypatch):
        """Asking for the Kuzu backend on a no-kuzu install must fail
        loudly with the install hint, not silently fall through."""
        _without_kuzu(monkeypatch)
        monkeypatch.setenv("CGH_DB", "kuzu")
        reset_connection()
        with pytest.raises(KuzuNotInstalled) as exc:
            get_connection(tmp_path)
        assert "pip install cgh[kuzu]" in str(exc.value)
        reset_connection()


class TestCliCatchesKuzuMissing:
    """The CLI dispatch should print a clean message + remediation for a
    KuzuNotInstalled error, not a Python traceback, unless --verbose."""

    def _run_main(self, monkeypatch, argv):
        import codegraph.__main__ as cli

        monkeypatch.setattr(sys, "argv", argv)
        return cli.main

    def test_index_without_kuzu_prints_clean_message(
        self, tmp_path, monkeypatch, capsys
    ):
        # A repo whose only graph DB is a Kuzu graph.db, with kuzu masked.
        # Needs a real source file so indexing reaches get_connection.
        cg = tmp_path / ".codegraph"
        cg.mkdir()
        (cg / "graph.db").write_bytes(b"fake")
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        _without_kuzu(monkeypatch)
        monkeypatch.delenv("CGH_DB", raising=False)
        reset_connection()

        main = self._run_main(monkeypatch, ["cgh", "index", "--root", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

        out = capsys.readouterr().out
        assert "Kuzu backend not available" in out
        assert "cgh migrate-to-duckdb" in out
        # `cgh[kuzu]` must survive rendering: Rich would strip [kuzu] as
        # markup if the body weren't wrapped in a literal Text.
        assert "cgh[kuzu]" in out
        # The clean path must NOT dump a Python traceback.
        assert "Traceback (most recent call last)" not in out
        reset_connection()

    def test_verbose_reraises_for_traceback(self, tmp_path, monkeypatch):
        cg = tmp_path / ".codegraph"
        cg.mkdir()
        (cg / "graph.db").write_bytes(b"fake")
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        _without_kuzu(monkeypatch)
        monkeypatch.delenv("CGH_DB", raising=False)
        reset_connection()

        main = self._run_main(
            monkeypatch, ["cgh", "index", "--root", str(tmp_path), "--verbose"]
        )
        # With --verbose the exception propagates so the stack is visible.
        with pytest.raises(KuzuNotInstalled):
            main()
        reset_connection()
