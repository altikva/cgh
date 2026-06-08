# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the optional Ruby tree-sitter parser. Skipped when the
#              `langs` extra (tree-sitter-ruby) is not installed.

from __future__ import annotations

import textwrap

import pytest

# Optional grammar: skip the whole module when the extra is absent, the same
# way tests/test_core/test_kuzu_optional.py guards the kuzu extra.
pytest.importorskip("tree_sitter_ruby")

from codegraph.core.db import get_connection, reset_connection  # noqa: E402
from codegraph.indexer import index_file  # noqa: E402
from codegraph.parsers.ruby import RubyParser  # noqa: E402


@pytest.fixture
def sample_ruby(tmp_path):
    f = tmp_path / "greeter.rb"
    f.write_text(
        textwrap.dedent("""\
        require "json"
        require_relative "helper"

        module Greetings
          class Greeter < Base
            def initialize(name)
              @name = name
            end

            def hello
              puts format_name(@name)
            end
          end

          def self.top
            helper
          end
        end
        """)
    )
    return f


class TestRubyParser:
    def test_class_and_module_extracted(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        kinds = {c.name: c.kind for c in idx.classes}
        assert kinds.get("Greeter") == "class"
        assert kinds.get("Greetings") == "module"

    def test_superclass_recorded(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        greeter = next(c for c in idx.classes if c.name == "Greeter")
        assert "Base" in greeter.bases

    def test_methods_attached_to_class(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        hello = next(
            f for f in idx.functions if f.name == "hello" and f.class_name == "Greeter"
        )
        assert hello.kind == "method"
        init = next(f for f in idx.functions if f.name == "initialize")
        assert init.class_name == "Greeter"

    def test_singleton_method(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        top = next(f for f in idx.functions if f.name == "top")
        assert top.kind == "singleton_method"
        assert top.class_name == "Greetings"

    def test_requires_as_imports(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        modules = [imp.source_module for imp in idx.imports]
        assert "json" in modules
        assert "helper" in modules

    def test_calls_collected(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        hello = next(f for f in idx.functions if f.name == "hello")
        assert "puts" in hello.calls
        assert "format_name" in hello.calls

    def test_malformed_does_not_raise(self, tmp_path):
        f = tmp_path / "broken.rb"
        f.write_text("class def end module (((")
        idx = RubyParser().parse(f)
        assert idx.lang == "ruby"

    def test_lang(self, sample_ruby):
        idx = RubyParser().parse(sample_ruby)
        assert idx.lang == "ruby"


class TestRubyRoundTrip:
    @pytest.fixture(autouse=True)
    def clean_db(self):
        reset_connection()
        yield
        reset_connection()

    def test_index_file_lands_symbols(self, sample_ruby, tmp_path):
        ok = index_file(sample_ruby, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        files = conn.find_nodes("File", return_fields=["path", "lang"])
        assert any(fi["lang"] == "ruby" for fi in files)

        cls_names = [
            c["name"] for c in conn.find_nodes("Class", return_fields=["name"])
        ]
        assert "Greeter" in cls_names

        fn_names = [
            f["name"] for f in conn.find_nodes("Function", return_fields=["name"])
        ]
        assert "hello" in fn_names
