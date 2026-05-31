"""Tests for the Go tree-sitter parser."""

from __future__ import annotations

import textwrap

import pytest

from codegraph.parsers.golang import GoParser


@pytest.fixture
def sample_go(tmp_path):
    f = tmp_path / "greet.go"
    f.write_text(
        textwrap.dedent("""\
        package main

        import (
            "fmt"
            "strings"
        )

        type Greeter struct {
            Name string
        }

        type Speaker interface {
            Hello() string
        }

        func (g *Greeter) Hello() string {
            return fmt.Sprintf("hi %s", strings.ToUpper(g.Name))
        }

        func main() {
            g := Greeter{Name: "world"}
            fmt.Println(g.Hello())
        }
        """)
    )
    return f


class TestGoParser:
    def test_parses_functions(self, sample_go):
        idx = GoParser().parse(sample_go)
        names = {f.name for f in idx.functions}
        assert "main" in names
        assert "Hello" in names

    def test_method_receiver_attached_to_class(self, sample_go):
        idx = GoParser().parse(sample_go)
        hello = next(f for f in idx.functions if f.name == "Hello")
        assert hello.class_name == "Greeter"
        assert hello.kind == "method"

    def test_struct_and_interface(self, sample_go):
        idx = GoParser().parse(sample_go)
        classes = {c.name: c.kind for c in idx.classes}
        assert classes.get("Greeter") == "struct"
        assert classes.get("Speaker") == "interface"

    def test_imports(self, sample_go):
        idx = GoParser().parse(sample_go)
        modules = [imp.source_module for imp in idx.imports]
        assert "fmt" in modules
        assert "strings" in modules

    def test_calls_collected(self, sample_go):
        idx = GoParser().parse(sample_go)
        hello = next(f for f in idx.functions if f.name == "Hello")
        # Sprintf, ToUpper called in method body
        assert "Sprintf" in hello.calls
        assert "ToUpper" in hello.calls

    def test_lang(self, sample_go):
        idx = GoParser().parse(sample_go)
        assert idx.lang == "go"
