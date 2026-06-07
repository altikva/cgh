# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: C# parser plugin. Extracts classes, interfaces, structs, enums,
#              methods, using directives (imports), and call references using
#              tree-sitter-c-sharp. Optional: ships behind the `langs` extra,
#              so the grammar import only happens when the extra is installed.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

CSHARP_LANGUAGE = Language(tscs.language())
_parser = Parser(CSHARP_LANGUAGE)

# Type declarations that map to a ClassDef, with their codegraph kind.
_TYPE_DECLS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "struct_declaration": "struct",
    "enum_declaration": "enum",
    "record_declaration": "record",
}


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Walk a C# method body, return called method names (deduped).

    Covers invocation_expression (`obj.Method()`, `Method()`) and
    object_creation_expression (`new Foo()`).
    """
    calls: list[str] = []
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type == "invocation_expression":
            fn = n.child_by_field_name("function")
            if fn:
                name = _ident(fn, src)
                # `obj.Method` or `Foo.Bar.Method` -> last segment
                if "." in name:
                    name = name.split(".")[-1]
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        elif n.type == "object_creation_expression":
            type_node = n.child_by_field_name("type")
            if type_node:
                name = _ident(type_node, src)
                if "." in name:
                    name = name.split(".")[-1]
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


@register_parser(".cs")
class CSharpParser(BaseParser):
    """Tree-sitter parser for C# source files."""

    lang = "csharp"
    extensions = [".cs"]
    extracts = ["classes", "interfaces", "methods", "imports", "calls"]
    description = "C# source files (.cs)"
    tree_sitter_lang = "c_sharp"

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
            body_node = method_node.child_by_field_name("body")
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
                    start_line=method_node.start_point[0] + 1,
                    end_line=method_node.end_point[0] + 1,
                    docstring="",
                    class_name=current_class,
                    calls=calls,
                    kind="constructor"
                    if method_node.type == "constructor_declaration"
                    else "method",
                )
            )

        def _emit_type(decl: Node, kind: str) -> None:
            name_node = decl.child_by_field_name("name")
            if not name_node:
                return
            name = _ident(name_node, src)
            bases: list[str] = []
            # `base_list` (`: Base, IFoo`) is a positional child, not a named
            # field, so look it up by node type.
            for child in decl.children:
                if child.type == "base_list":
                    for b in child.children:
                        if b.type in ("identifier", "qualified_name", "generic_name"):
                            bases.append(_ident(b, src))
                    break
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
                    if child.type in ("method_declaration", "constructor_declaration"):
                        _emit_method(child, name)
                    elif child.type in _TYPE_DECLS:
                        _emit_type(child, _TYPE_DECLS[child.type])

        def _emit_using(decl: Node) -> None:
            # `using System;` / `using System.Collections.Generic;`
            # Skip the leading `using` keyword and any alias `=`; the module is
            # the identifier or qualified_name child.
            for child in decl.children:
                if child.type in ("identifier", "qualified_name"):
                    mod = _text(child, src)
                    if mod:
                        index.imports.append(ImportRef(source_module=mod, symbols=[]))
                    return

        def _walk(node: Node) -> None:
            # Namespaces (block or file-scoped) just wrap declarations, so
            # recurse into them rather than treating them as types.
            for child in node.children:
                t = child.type
                if t == "using_directive":
                    _emit_using(child)
                elif t in _TYPE_DECLS:
                    _emit_type(child, _TYPE_DECLS[t])
                elif t in (
                    "namespace_declaration",
                    "file_scoped_namespace_declaration",
                    "declaration_list",
                ):
                    _walk(child)

        _walk(root)
        return index
