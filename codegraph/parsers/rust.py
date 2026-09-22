# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
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

        def _use_paths(node: Node, prefix: str = "") -> list[str]:
            """Every module path a use tree names.

            `use crate::{a::X, b::Y}` names two modules, not one, and each is
            its own edge. Collapsing the tree to a single string would leave
            the grouped form, which is the idiomatic one in Rust, resolving
            to nothing at all.
            """
            kind = node.type
            if kind in ("identifier", "scoped_identifier", "crate", "super", "self"):
                tail = _text(node, src).strip()
                return [f"{prefix}{tail}" if prefix else tail]
            if kind == "use_as_clause":
                target = node.child_by_field_name("path")
                return _use_paths(target, prefix) if target else []
            if kind == "use_wildcard":
                inner = node.child_by_field_name("path") or (
                    node.children[0] if node.children else None
                )
                return _use_paths(inner, prefix) if inner else []
            if kind == "scoped_use_list":
                head = node.child_by_field_name("path")
                lst = node.child_by_field_name("list")
                base = prefix + (f"{_text(head, src).strip()}::" if head else "")
                return _use_paths(lst, base) if lst else []
            if kind == "use_list":
                out: list[str] = []
                for child in node.children:
                    if child.type in (",", "{", "}"):
                        continue
                    out.extend(_use_paths(child, prefix))
                return out
            return []

        def _emit_use(use: Node) -> None:
            for child in use.children:
                if child.type in (
                    "identifier",
                    "scoped_identifier",
                    "scoped_use_list",
                    "use_as_clause",
                    "use_list",
                    "use_wildcard",
                ):
                    for path in _use_paths(child):
                        if path:
                            index.imports.append(
                                ImportRef(source_module=path, symbols=[])
                            )
                    return

        def _emit_mod(mod: Node) -> None:
            """A body-less `mod foo;` is how a Rust file reaches a sibling.

            Rust has no import statement for a crate's own files: the parent
            module declares the child, and every `use crate::foo` elsewhere
            leans on that declaration. Skip it and a crate's file tree stays
            disconnected whatever its `use` lines say. An inline
            `mod foo { ... }` declares nothing outside this file, so it
            carries no edge.
            """
            if any(child.type == "declaration_list" for child in mod.children):
                return
            name = mod.child_by_field_name("name")
            if name is None:
                return
            ident = _text(name, src).strip()
            if ident:
                index.imports.append(
                    ImportRef(source_module=f"self::{ident}", symbols=[])
                )

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
            elif node.type == "mod_item":
                _emit_mod(node)
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
