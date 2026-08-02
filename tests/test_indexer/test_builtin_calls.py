"""
Tests for the language-builtin CALLS filter (codegraph.parsers.builtins).

The indexer skips CALLS edges whose callee name matches a language built-in
so callees like ``isinstance``, ``len``, ``String``, ``parseInt`` don't
accumulate spurious edges from every call site. The conscious tradeoff:
if user code happens to define a function shadowing a built-in (rare),
the edge to that user-defined function is also skipped.
"""

from __future__ import annotations

import textwrap

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file
from codegraph.parsers.builtins import is_builtin


@pytest.fixture(autouse=True)
def clean_db():
    reset_connection()
    yield
    reset_connection()


class TestIsBuiltin:
    def test_python_builtins_recognised(self):
        for name in ("isinstance", "len", "print", "str", "type", "super"):
            assert is_builtin("python", name), f"{name} should be a python builtin"

    def test_typescript_builtins_recognised(self):
        for name in ("String", "Number", "Promise", "parseInt", "console"):
            assert is_builtin("typescript", name), f"{name} should be a TS builtin"

    def test_javascript_aliases_to_typescript_set(self):
        # We treat JS and TS as the same set — they share the runtime globals.
        assert is_builtin("javascript", "Promise")
        assert is_builtin("javascript", "parseInt")

    def test_vue_uses_js_ts_set(self):
        assert is_builtin("vue", "Promise")

    def test_unknown_lang_skips_filtering(self):
        # No filter for languages we don't have a list for — safe default.
        assert not is_builtin("ruby", "puts")
        assert not is_builtin("", "anything")

    def test_unknown_name_not_builtin(self):
        assert not is_builtin("python", "my_custom_function")
        assert not is_builtin("typescript", "myHelper")


class TestBuiltinCallsFiltered:
    def test_shadowed_python_builtin_gets_no_edge(self, tmp_path):
        """User defines `def len(x): ...` then calls it. No CALLS edge
        should form, because we skip every call to a name matching a
        builtin regardless of whether a user Function shadows it.
        """
        f = tmp_path / "shadow.py"
        f.write_text(
            textwrap.dedent("""\
            def len(x):
                return 5

            def main():
                len([1, 2, 3])
            """)
        )
        ok = index_file(f, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors("CALLS", dst_where={"name": "len"})
        assert len(edges) == 0, (
            "expected zero CALLS edges to a builtin-shadowing function"
        )

    def test_non_builtin_calls_still_resolve(self, tmp_path):
        """Sanity: filtering must not affect legitimate user-to-user edges."""
        f = tmp_path / "normal.py"
        f.write_text(
            textwrap.dedent("""\
            def helper(x):
                return x * 2

            def main():
                helper(42)
            """)
        )
        index_file(f, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors(
            "CALLS",
            src_where={"name": "main"},
            dst_where={"name": "helper"},
        )
        assert len(edges) == 1, "expected a CALLS edge from main to helper"

    def test_python_isinstance_call_skipped(self, tmp_path):
        """A user file calling isinstance() should not create a CALLS edge
        even if a separately-indexed file defines a function named
        isinstance. We test the indexer's filter, not the resolver — so
        we set up the shadow definition first, then the caller file.
        """
        # File 1: defines a function named like a builtin
        f1 = tmp_path / "shadow.py"
        f1.write_text(
            textwrap.dedent("""\
            def isinstance(x, klass):
                return True
            """)
        )
        # File 2: calls isinstance from main
        f2 = tmp_path / "caller.py"
        f2.write_text(
            textwrap.dedent("""\
            def main(obj):
                if isinstance(obj, dict):
                    return obj
                return None
            """)
        )
        index_file(f1, tmp_path)
        index_file(f2, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors(
            "CALLS",
            src_where={"name": "main"},
            dst_where={"name": "isinstance"},
        )
        assert len(edges) == 0, "main → isinstance edge should be filtered"

    def test_typescript_builtin_call_skipped(self, tmp_path):
        """parseInt / Number / String calls should not produce CALLS
        edges to a user function with the same name."""
        f1 = tmp_path / "shadow.ts"
        f1.write_text(
            textwrap.dedent("""\
            export function parseInt(s: string): number {
                return 0;
            }
            """)
        )
        f2 = tmp_path / "caller.ts"
        f2.write_text(
            textwrap.dedent("""\
            export function main() {
                const n = parseInt("42");
                return n;
            }
            """)
        )
        index_file(f1, tmp_path)
        index_file(f2, tmp_path)

        conn = get_connection(tmp_path)
        edges = conn.find_neighbors(
            "CALLS",
            src_where={"name": "main"},
            dst_where={"name": "parseInt"},
        )
        assert len(edges) == 0, "main → parseInt edge should be filtered"
