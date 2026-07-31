# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Ruby parser plugin. Extracts classes and modules, methods
#              (`def` and `def self.`), require / require_relative (imports),
#              and call references using tree-sitter-ruby. Optional: ships
#              behind the `langs` extra, so the grammar import only happens
#              when the extra is installed.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_ruby as tsr
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

RUBY_LANGUAGE = Language(tsr.language())
_parser = Parser(RUBY_LANGUAGE)

_REQUIRE_NAMES = {"require", "require_relative", "load", "autoload"}


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _string_value(node: Node, src: bytes) -> str:
    """Pull the literal text out of a `string` node, dropping the quotes."""
    for child in node.children:
        if child.type == "string_content":
            return _text(child, src)
    return _text(node, src).strip("\"'")


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Walk a Ruby method body, return called method names (deduped).

    A Ruby `call` node is `recv.method(args)` or a bare `method(args)`;
    the method name sits in the `method` field (or is the lone identifier
    for a paren-less call).
    """
    calls: list[str] = []
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type == "call":
            method = n.child_by_field_name("method")
            if method is not None:
                name = _ident(method, src)
                if re.match(r"^\w+[?!]?$", name, re.UNICODE):
                    calls.append(name.rstrip("?!"))
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


@register_parser(".rb")
class RubyParser(BaseParser):
    """Tree-sitter parser for Ruby source files."""

    lang = "ruby"
    extensions = [".rb"]
    extracts = ["classes", "modules", "methods", "imports", "calls"]
    description = "Ruby source files (.rb)"
    tree_sitter_lang = "ruby"

    def parse(self, path: Path) -> FileIndex:
        path = Path(path)
        path_str = str(path)
        src = path.read_bytes()
        tree = _parser.parse(src)
        root = tree.root_node

        index = FileIndex(path=path_str, lang=self.lang)

        def _emit_method(method_node: Node, current_class: str | None) -> None:
            name_node = method_node.child_by_field_name("name")
            name = _ident(name_node, src) if name_node else "?"
            calls = _collect_calls(method_node, src)
            is_singleton = method_node.type == "singleton_method"
            fn_id = (
                f"{path_str}::{current_class}.{name}"
                if current_class
                else f"{path_str}::{name}"
            )
            index.functions.append(
                SymbolDef(
                    id=fn_id,
                    name=name,
                    file_path=path_str,
                    start_line=method_node.start_point[0] + 1,
                    end_line=method_node.end_point[0] + 1,
                    docstring="",
                    class_name=current_class,
                    calls=calls,
                    kind="singleton_method" if is_singleton else "method",
                )
            )

        def _emit_type(decl: Node, kind: str) -> None:
            name_node = decl.child_by_field_name("name")
            if not name_node:
                return
            name = _ident(name_node, src)
            bases: list[str] = []
            if kind == "class":
                superclass = decl.child_by_field_name("superclass")
                if superclass:
                    for child in superclass.children:
                        if child.type in ("constant", "scope_resolution"):
                            bases.append(_ident(child, src))
            index.classes.append(
                ClassDef(
                    id=f"{path_str}::{name}",
                    name=name,
                    file_path=path_str,
                    start_line=decl.start_point[0] + 1,
                    end_line=decl.end_point[0] + 1,
                    docstring="",
                    bases=bases,
                    kind=kind,
                )
            )
            body = decl.child_by_field_name("body")
            if body:
                for child in body.children:
                    _dispatch(child, name)

        def _emit_require(call_node: Node) -> None:
            method = call_node.child_by_field_name("method")
            if not method or _ident(method, src) not in _REQUIRE_NAMES:
                return
            args = call_node.child_by_field_name("arguments")
            if not args:
                return
            for arg in args.children:
                if arg.type == "string":
                    mod = _string_value(arg, src)
                    if mod:
                        index.imports.append(ImportRef(source_module=mod, symbols=[]))
                    return

        def _dispatch(node: Node, current_class: str | None) -> None:
            t = node.type
            if t == "class":
                _emit_type(node, "class")
            elif t == "module":
                _emit_type(node, "module")
            elif t in ("method", "singleton_method"):
                _emit_method(node, current_class)
            elif t == "call":
                _emit_require(node)

        for node in root.children:
            _dispatch(node, None)

        return index
