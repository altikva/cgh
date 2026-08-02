# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Go parser plugin. Extracts functions, methods, structs,
# interfaces, imports, and call references using tree-sitter-go.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_go as tsg
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

GO_LANGUAGE = Language(tsg.language())
_parser = Parser(GO_LANGUAGE)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _strip_quotes(s: str) -> str:
    return s.strip().strip("\"'`")


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Walk a Go function body, return called function names (deduped)."""
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
                # `pkg.Func` or `obj.Method` -> last segment
                if "." in name:
                    name = name.split(".")[-1]
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


@register_parser(".go")
class GoParser(BaseParser):
    """Tree-sitter parser for Go source files."""

    lang = "go"
    extensions = [".go"]
    extracts = ["functions", "classes", "imports", "calls"]
    description = "Go source files (.go)"
    tree_sitter_lang = "go"

    def parse(self, path: Path) -> FileIndex:
        path = Path(path)
        path_str = str(path)
        src = path.read_bytes()
        tree = _parser.parse(src)
        root = tree.root_node

        index = FileIndex(path=path_str, lang=self.lang)

        def _emit_function(fn_node: Node, receiver: str | None) -> None:
            name_node = fn_node.child_by_field_name("name")
            name = _ident(name_node, src) if name_node else "?"
            body_node = fn_node.child_by_field_name("body")
            calls = _collect_calls(body_node, src) if body_node else []
            fn_id = (
                f"{path_str}::{receiver}.{name}" if receiver else f"{path_str}::{name}"
            )
            index.functions.append(
                SymbolDef(
                    id=fn_id,
                    name=name,
                    file_path=path_str,
                    start_line=fn_node.start_point[0] + 1,
                    end_line=fn_node.end_point[0] + 1,
                    docstring="",
                    class_name=receiver,
                    calls=calls,
                    kind="method" if receiver else "function",
                )
            )

        def _receiver_type(method_node: Node) -> str | None:
            """Pull the receiver's base type name out of a method_declaration.

            ``func (g *Greeter) Hello() ...`` -> ``Greeter``.
            """
            recv = method_node.child_by_field_name("receiver")
            if not recv:
                return None
            for child in recv.children:
                if child.type == "parameter_declaration":
                    t = child.child_by_field_name("type")
                    if not t:
                        continue
                    if t.type == "pointer_type":
                        inner = t.child(1)
                        return _ident(inner, src) if inner else None
                    return _ident(t, src)
            return None

        def _emit_type_decl(type_spec: Node) -> None:
            name_node = type_spec.child_by_field_name("name")
            name = _ident(name_node, src) if name_node else "?"
            kind_node = type_spec.child_by_field_name("type")
            kind = "struct"
            if kind_node and kind_node.type == "interface_type":
                kind = "interface"
            index.classes.append(
                ClassDef(
                    id=f"{path_str}::{name}",
                    name=name,
                    file_path=path_str,
                    start_line=type_spec.start_point[0] + 1,
                    end_line=type_spec.end_point[0] + 1,
                    docstring="",
                    bases=[],
                    kind=kind,
                )
            )

        for node in root.children:
            if node.type == "import_declaration":
                for child in node.children:
                    if child.type == "import_spec_list":
                        for spec in child.children:
                            if spec.type == "import_spec":
                                path_node = spec.child_by_field_name("path")
                                if path_node:
                                    mod = _strip_quotes(_text(path_node, src))
                                    if mod:
                                        index.imports.append(
                                            ImportRef(source_module=mod, symbols=[])
                                        )
                    elif child.type == "import_spec":
                        path_node = child.child_by_field_name("path")
                        if path_node:
                            mod = _strip_quotes(_text(path_node, src))
                            if mod:
                                index.imports.append(
                                    ImportRef(source_module=mod, symbols=[])
                                )

            elif node.type == "type_declaration":
                for child in node.children:
                    if child.type == "type_spec":
                        _emit_type_decl(child)

            elif node.type == "function_declaration":
                _emit_function(node, None)

            elif node.type == "method_declaration":
                _emit_function(node, _receiver_type(node))

        return index
