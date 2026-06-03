"""
Tests for normalize_identifier — NFKC collapse so visually-identical
identifier forms produce the same string.
"""

from __future__ import annotations

import textwrap

from codegraph.core.db import get_connection, reset_connection
from codegraph.core.utils import normalize_identifier
from codegraph.indexer import index_file


class TestNormalizeIdentifier:
    def test_composed_decomposed_collapse(self):
        """U+00E9 (é) and 'e' + U+0301 (combining acute) collapse to the same."""
        composed = "café"
        decomposed = "café"
        assert composed != decomposed  # byte representations differ
        assert normalize_identifier(composed) == normalize_identifier(decomposed)

    def test_fullwidth_collapses_to_ascii(self):
        """Fullwidth latin letters collapse to ASCII."""
        fullwidth = "Ｆｏｏ"  # FULLWIDTH F, O, O
        assert normalize_identifier(fullwidth) == "Foo"

    def test_ascii_passes_through(self):
        assert normalize_identifier("my_function") == "my_function"
        assert normalize_identifier("MyClass") == "MyClass"

    def test_empty_string(self):
        assert normalize_identifier("") == ""

    def test_cjk_unchanged(self):
        """CJK characters are already in NFKC form, should pass through."""
        cjk = "計算"
        assert normalize_identifier(cjk) == cjk

    def test_ligature_decomposed(self):
        """ﬁ ligature (U+FB01) decomposes to 'fi' under NFKC."""
        assert normalize_identifier("ﬁne") == "fine"


class TestParserNormalization:
    def setup_method(self):
        reset_connection()

    def teardown_method(self):
        reset_connection()

    def test_python_composed_vs_decomposed_collapse(self, tmp_path):
        """A function named with U+00E9 and one with e+U+0301 should
        produce the same Function.name (and id), so they don't fork
        into two separate nodes."""
        f = tmp_path / "unicode_funcs.py"
        f.write_text(
            textwrap.dedent("""\
            def café():
                return 1

            def caller():
                café()
            """),
            encoding="utf-8",
        )
        index_file(f, tmp_path)

        conn = get_connection(tmp_path)
        names = [
            f["name"]
            for f in conn.find_nodes("Function", return_fields=["name"], order_by=["name"])
        ]
        # We expect "café" (NFKC composed) appearing once, plus the caller.
        assert names.count("café") == 1, (
            f"expected one normalized 'café' entry, got names={names}"
        )

    def test_python_fullwidth_normalized(self, tmp_path):
        """def Ｆｏｏ(): ... should be stored as 'Foo' after NFKC."""
        f = tmp_path / "fullwidth.py"
        f.write_text(
            "def Ｆoo():\n    pass\n",
            encoding="utf-8",
        )
        index_file(f, tmp_path)

        conn = get_connection(tmp_path)
        names = [f["name"] for f in conn.find_nodes("Function", return_fields=["name"])]
        assert "Foo" in names, f"fullwidth Ｆoo should normalize to 'Foo', got {names}"

    def test_typescript_composed_decomposed_collapse(self, tmp_path):
        """Same NFKC behaviour for TS."""
        f = tmp_path / "unicode.ts"
        f.write_text(
            textwrap.dedent("""\
            export function café() {
                return 1;
            }

            export function caller() {
                café();
            }
            """),
            encoding="utf-8",
        )
        index_file(f, tmp_path)

        conn = get_connection(tmp_path)
        names = [
            f["name"]
            for f in conn.find_nodes("Function", return_fields=["name"], order_by=["name"])
        ]
        assert names.count("café") == 1, (
            f"expected one normalized 'café' entry, got names={names}"
        )

    def test_cjk_identifiers_extracted(self, tmp_path):
        """Non-ASCII identifiers no longer dropped by the regex filter
        (previously matched only [A-Za-z_]). CJK should produce nodes
        and a CALLS edge."""
        f = tmp_path / "cjk.py"
        f.write_text(
            textwrap.dedent("""\
            def 計算():
                return 42

            def main():
                計算()
            """),
            encoding="utf-8",
        )
        index_file(f, tmp_path)

        conn = get_connection(tmp_path)
        names = [
            e["dst_name"]
            for e in conn.find_neighbors(
                "CALLS", src_where={"name": "main"}, return_dst=["name"]
            )
        ]
        assert "計算" in names, (
            f"expected CALLS edge to CJK function, got names={names}"
        )
