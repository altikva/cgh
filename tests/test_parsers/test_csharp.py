# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the optional C# tree-sitter parser. Skipped when the
#              `langs` extra (tree-sitter-c-sharp) is not installed.

from __future__ import annotations

import textwrap

import pytest

# Optional grammar: skip the whole module when the extra is absent, the same
# way tests/test_core/test_kuzu_optional.py guards the kuzu extra.
pytest.importorskip("tree_sitter_c_sharp")

from codegraph.core.db import get_connection, reset_connection  # noqa: E402
from codegraph.indexer import index_file  # noqa: E402
from codegraph.parsers.csharp import CSharpParser  # noqa: E402


@pytest.fixture
def sample_csharp(tmp_path):
    f = tmp_path / "Greeter.cs"
    f.write_text(
        textwrap.dedent("""\
        using System;
        using System.Collections.Generic;

        namespace MyApp.Core
        {
            interface ISpeaker
            {
                string Hello();
            }

            public class Greeter : Base, ISpeaker
            {
                private string name;

                public Greeter(string name)
                {
                    this.name = name;
                }

                public string Hello()
                {
                    var parts = new List<string>();
                    return Format(parts);
                }
            }

            class Program
            {
                static void Main()
                {
                    var g = new Greeter("world");
                    Console.WriteLine(g.Hello());
                }
            }
        }
        """)
    )
    return f


class TestCSharpParser:
    def test_classes_extracted(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        kinds = {c.name: c.kind for c in idx.classes}
        assert kinds.get("Greeter") == "class"
        assert kinds.get("Program") == "class"
        assert kinds.get("ISpeaker") == "interface"

    def test_constructor_attached_to_class(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        constructors = [f for f in idx.functions if f.kind == "constructor"]
        assert any(
            c.name == "Greeter" and c.class_name == "Greeter" for c in constructors
        )

    def test_methods_attached_to_class(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        hello = next(
            f for f in idx.functions if f.name == "Hello" and f.class_name == "Greeter"
        )
        assert hello.kind == "method"

    def test_base_list_recorded(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        greeter = next(c for c in idx.classes if c.name == "Greeter")
        assert "Base" in greeter.bases
        assert "ISpeaker" in greeter.bases

    def test_using_imports(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        modules = [imp.source_module for imp in idx.imports]
        assert "System" in modules
        assert "System.Collections.Generic" in modules

    def test_calls_include_new_and_invocation(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        main = next(f for f in idx.functions if f.name == "Main")
        # `new Greeter(...)` from object_creation_expression
        assert "Greeter" in main.calls
        # `Console.WriteLine(...)` from invocation_expression -> last segment
        assert "WriteLine" in main.calls

    def test_file_scoped_namespace(self, tmp_path):
        f = tmp_path / "Scoped.cs"
        f.write_text(
            textwrap.dedent("""\
            namespace Acme;

            public class Widget
            {
                public void Spin() { }
            }
            """)
        )
        idx = CSharpParser().parse(f)
        assert any(c.name == "Widget" for c in idx.classes)

    def test_malformed_does_not_raise(self, tmp_path):
        f = tmp_path / "Broken.cs"
        f.write_text("public class { void (")
        # Must return a FileIndex, never raise.
        idx = CSharpParser().parse(f)
        assert idx.lang == "csharp"

    def test_lang(self, sample_csharp):
        idx = CSharpParser().parse(sample_csharp)
        assert idx.lang == "csharp"


class TestCSharpRoundTrip:
    @pytest.fixture(autouse=True)
    def clean_db(self):
        reset_connection()
        yield
        reset_connection()

    def test_index_file_lands_symbols(self, sample_csharp, tmp_path):
        ok = index_file(sample_csharp, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        files = conn.find_nodes("File", return_fields=["path", "lang"])
        assert any(fi["lang"] == "csharp" for fi in files)

        cls_names = [
            c["name"] for c in conn.find_nodes("Class", return_fields=["name"])
        ]
        assert "Greeter" in cls_names

        fn_names = [
            f["name"] for f in conn.find_nodes("Function", return_fields=["name"])
        ]
        assert "Hello" in fn_names
