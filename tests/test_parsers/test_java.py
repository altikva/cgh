"""Tests for the Java tree-sitter parser."""

from __future__ import annotations

import textwrap

import pytest

from codegraph.parsers.java import JavaParser


@pytest.fixture
def sample_java(tmp_path):
    f = tmp_path / "Greeter.java"
    f.write_text(
        textwrap.dedent("""\
        package com.example;

        import java.util.List;
        import java.util.ArrayList;

        interface Speaker {
            String hello();
        }

        class Greeter implements Speaker {
            String name;

            public Greeter(String name) {
                this.name = name;
            }

            public String hello() {
                List<String> parts = new ArrayList<>();
                parts.add("hi");
                parts.add(this.name);
                return String.join(" ", parts);
            }
        }

        class Main {
            public static void main(String[] args) {
                Greeter g = new Greeter("world");
                System.out.println(g.hello());
            }
        }
        """)
    )
    return f


class TestJavaParser:
    def test_classes_extracted(self, sample_java):
        idx = JavaParser().parse(sample_java)
        kinds = {c.name: c.kind for c in idx.classes}
        assert kinds.get("Greeter") == "class"
        assert kinds.get("Main") == "class"
        assert kinds.get("Speaker") == "interface"

    def test_constructor_attached_to_class(self, sample_java):
        idx = JavaParser().parse(sample_java)
        constructors = [f for f in idx.functions if f.kind == "constructor"]
        assert any(
            c.name == "Greeter" and c.class_name == "Greeter" for c in constructors
        )

    def test_methods_attached_to_class(self, sample_java):
        idx = JavaParser().parse(sample_java)
        hello = next(
            f for f in idx.functions if f.name == "hello" and f.class_name == "Greeter"
        )
        assert hello.kind == "method"

    def test_implements_recorded(self, sample_java):
        idx = JavaParser().parse(sample_java)
        greeter = next(c for c in idx.classes if c.name == "Greeter")
        assert "Speaker" in greeter.bases

    def test_imports(self, sample_java):
        idx = JavaParser().parse(sample_java)
        modules = [imp.source_module for imp in idx.imports]
        assert any("java.util.List" in m for m in modules)
        assert any("java.util.ArrayList" in m for m in modules)

    def test_calls_include_new(self, sample_java):
        idx = JavaParser().parse(sample_java)
        # main calls new Greeter and println
        main = next(f for f in idx.functions if f.name == "main")
        # "Greeter" comes from object_creation_expression
        assert "Greeter" in main.calls
        # println from method_invocation
        assert "println" in main.calls

    def test_lang(self, sample_java):
        idx = JavaParser().parse(sample_java)
        assert idx.lang == "java"
