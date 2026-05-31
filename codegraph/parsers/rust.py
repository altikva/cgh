# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rust parser plugin. Extracts functions, structs, enums,
# traits, use declarations, and call references using tree-sitter-rust.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_rust as tsr
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

RUST_LANGUAGE = Language(tsr.language())
_parser = Parser(RUST_LANGUAGE)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Walk a Rust function body, return called function names (deduped).

    Covers both regular call_expression and macro_invocation. For macros,
    the trailing ``!`` is stripped so ``println!`` is collected as ``println``.
    """
    calls: list[str] = []
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn:
                name = _ident(fn, src)
                # `mod::Type::method` or `obj.method` -> last segment
                if "::" in name:
                    name = name.split("::")[-1]
                if "." in name:
                    name = name.split(".")[-1]
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        elif n.type == "macro_invocation":
            macro = n.child_by_field_name("macro")
            if macro:
                name = _ident(macro, src).rstrip("!")
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


@register_parser(".rs")
class RustParser(BaseParser):
    """Tree-sitter parser for Rust source files."""

    lang = "rust"
    extensions = [".rs"]
    extracts = ["functions", "structs", "enums", "traits", "imports", "calls"]
    description = "Rust source files (.rs)"
    tree_sitter_lang = "rust"

    def parse(self, path: Path) -> FileIndex:
        path = Path(path)
        path_str = str(path)
        src = path.read_bytes()
        tree = _parser.parse(src)
        root = tree.root_node

        index = FileIndex(path=path_str, lang=self.lang)

        def _emit_function(fn_node: Node, current_class: str | None = None) -> None:
            name_node = fn_node.child_by_field_name("name")
            name = _ident(name_node, src) if name_node else "?"
            body_node = fn_node.child_by_field_name("body")
            calls = _collect_calls(body_node, src) if body_node else []
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
                    start_line=fn_node.start_point[0] + 1,
                    end_line=fn_node.end_point[0] + 1,
                    docstring="",
                    class_name=current_class,
                    calls=calls,
                    kind="method" if current_class else "function",
                )
            )

        def _emit_type(decl: Node, kind: str) -> str | None:
            name_node = decl.child_by_field_name("name")
            if not name_node:
                return None
            name = _ident(name_node, src)
            index.classes.append(
                ClassDef(
                    id=f"{path_str}::{name}",
                    name=name,
                    file_path=path_str,
                    start_line=decl.start_point[0] + 1,
                    end_line=decl.end_point[0] + 1,
                    docstring="",
                    bases=[],
                    kind=kind,
                )
            )
            return name

        def _emit_use(use: Node) -> None:
            for child in use.children:
                if child.type in (
                    "scoped_identifier",
                    "scoped_use_list",
                    "use_as_clause",
                    "use_list",
                ):
                    text = _text(child, src).strip()
                    if text:
                        index.imports.append(ImportRef(source_module=text, symbols=[]))
                    return
                if child.type == "identifier":
                    index.imports.append(
                        ImportRef(source_module=_text(child, src), symbols=[])
                    )
                    return

        def _walk_impl(impl: Node) -> None:
            type_node = impl.child_by_field_name("type")
            current_class = _ident(type_node, src) if type_node else None
            body = impl.child_by_field_name("body")
            if not body:
                return
            for child in body.children:
                if child.type == "function_item":
                    _emit_function(child, current_class)

        for node in root.children:
            if node.type == "use_declaration":
                _emit_use(node)
            elif node.type == "struct_item":
                _emit_type(node, "struct")
            elif node.type == "enum_item":
                _emit_type(node, "enum")
            elif node.type == "trait_item":
                _emit_type(node, "trait")
            elif node.type == "function_item":
                _emit_function(node, None)
            elif node.type == "impl_item":
                _walk_impl(node)

        return index
