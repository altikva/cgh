"""
Tests for the safe-extract wrapper around parser.parse(path).

The indexer must never crash because one file is malformed or recurses
too deep. It logs to stderr + .codegraph/activity.log, returns False
from index_file, and lets the rest of the scan continue.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.core.utils import rows
from codegraph.indexer import _RECURSION_LIMIT, index_file


@pytest.fixture(autouse=True)
def clean_db():
    reset_connection()
    yield
    reset_connection()


class TestRecursionLimit:
    def test_recursion_limit_raised_at_import(self):
        """We bump the recursion limit at module import so tree-sitter walks
        on deeply-nested code don't crash before our handler kicks in."""
        assert sys.getrecursionlimit() >= _RECURSION_LIMIT


class TestParseErrorHandling:
    def test_corrupt_bytes_skipped_gracefully(self, tmp_path, capsys):
        """A file with invalid UTF-8 sequences shouldn't crash the indexer."""
        f = tmp_path / "broken.py"
        f.write_bytes(b"\xff\xfe\xfd invalid \x00\x00 def foo(): pass")

        ok = index_file(f, tmp_path)
        # Parser may decide it can still parse some of this; either way it
        # must not raise. If it skips, the return is False.
        assert ok in (True, False)
        # Whatever happens, no traceback leaked to stdout/stderr — only our
        # tagged log lines from the safe wrapper (if any).
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err

    def test_recursion_error_skipped(self, tmp_path, monkeypatch, capsys):
        """When a parser raises RecursionError, the file is skipped and a
        parse_error event is logged, no traceback escapes."""

        def _raising_parse(self, path):
            raise RecursionError("simulated")

        from codegraph.parsers import python as python_parser

        monkeypatch.setattr(python_parser.PythonParser, "parse", _raising_parse)

        f = tmp_path / "loops.py"
        f.write_text("def foo(): pass\n")

        ok = index_file(f, tmp_path)
        assert ok is False, "file with RecursionError should be skipped"

        captured = capsys.readouterr()
        assert "recursion_limit_exceeded" in captured.err
        assert "Traceback" not in captured.err

        # Activity log received the parse_error event
        from codegraph.activity import tail as _tail

        events = _tail(tmp_path, n=20)
        assert any(
            event == "parse_error" and "recursion_limit_exceeded" in detail
            for _ts, event, detail in events
        ), f"expected parse_error event in activity log, got {events}"

    def test_generic_parse_error_skipped(self, tmp_path, monkeypatch, capsys):
        """Any other parser exception (ValueError, AttributeError, ...) is
        caught the same way."""

        def _raising_parse(self, path):
            raise ValueError("synthetic parse failure")

        from codegraph.parsers import python as python_parser

        monkeypatch.setattr(python_parser.PythonParser, "parse", _raising_parse)

        f = tmp_path / "bad.py"
        f.write_text("def foo(): pass\n")

        ok = index_file(f, tmp_path)
        assert ok is False

        captured = capsys.readouterr()
        assert "ValueError" in captured.err
        assert "synthetic parse failure" in captured.err

    def test_other_files_unaffected_by_failure(self, tmp_path, monkeypatch):
        """If file B fails, files A and C should still index normally."""
        good_a = tmp_path / "good_a.py"
        good_a.write_text(
            textwrap.dedent("""\
            def a_func():
                return 1
            """)
        )
        bad_b = tmp_path / "bad_b.py"
        bad_b.write_text("def b_func(): pass\n")
        good_c = tmp_path / "good_c.py"
        good_c.write_text(
            textwrap.dedent("""\
            def c_func():
                return 3
            """)
        )

        # Index A normally
        assert index_file(good_a, tmp_path) is True

        # Monkey-patch the parser to fail on the next call (B)
        from codegraph.parsers import python as python_parser

        original_parse = python_parser.PythonParser.parse
        calls = {"count": 0}

        def _intermittent_parse(self, path):
            calls["count"] += 1
            if calls["count"] == 1:  # first call after patching = bad_b
                raise RuntimeError("boom on B")
            return original_parse(self, path)

        monkeypatch.setattr(python_parser.PythonParser, "parse", _intermittent_parse)

        assert index_file(bad_b, tmp_path) is False  # B fails gracefully
        assert index_file(good_c, tmp_path) is True  # C still works

        conn = get_connection(tmp_path)
        result = conn.execute("MATCH (fn:Function) RETURN fn.name ORDER BY fn.name")
        names = [row["fn.name"] for row in rows(result)]
        assert "a_func" in names
        assert "c_func" in names
        # b_func may or may not exist (the parser failed before producing it)
