"""Tests for the Rust tree-sitter parser."""

from __future__ import annotations

import textwrap

import pytest

from codegraph.parsers.rust import RustParser


@pytest.fixture
def sample_rust(tmp_path):
    f = tmp_path / "greet.rs"
    f.write_text(
        textwrap.dedent("""\
        use std::collections::HashMap;

        struct User {
            name: String,
        }

        enum Status { Active, Disabled }

        trait Greet {
            fn greet(&self);
        }

        impl User {
            fn new(name: String) -> Self {
                Self { name }
            }

            fn greet(&self) {
                println!("hi {}", self.name);
            }
        }

        fn main() {
            let u = User::new(String::from("world"));
            u.greet();
        }
        """)
    )
    return f


class TestRustParser:
    def test_function_at_root(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        names = {f.name for f in idx.functions}
        assert "main" in names

    def test_impl_methods_attached_to_struct(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        greet = next(f for f in idx.functions if f.name == "greet")
        assert greet.class_name == "User"
        assert greet.kind == "method"

    def test_struct_enum_trait(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        kinds = {c.name: c.kind for c in idx.classes}
        assert kinds.get("User") == "struct"
        assert kinds.get("Status") == "enum"
        assert kinds.get("Greet") == "trait"

    def test_use_imports(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        modules = [imp.source_module for imp in idx.imports]
        assert any("std::collections::HashMap" in m for m in modules)

    def test_calls_include_macros_without_bang(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        greet = next(f for f in idx.functions if f.name == "greet")
        # println! macro should produce a "println" call (no trailing !)
        assert "println" in greet.calls

    def test_calls_strip_path_separator(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        main = next(f for f in idx.functions if f.name == "main" and f.class_name is None)
        # User::new -> "new", String::from -> "from", u.greet() -> "greet"
        assert "new" in main.calls
        assert "from" in main.calls
        assert "greet" in main.calls

    def test_lang(self, sample_rust):
        idx = RustParser().parse(sample_rust)
        assert idx.lang == "rust"
