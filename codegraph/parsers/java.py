# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Java parser plugin. Extracts classes, interfaces, methods,
# imports, and call references using tree-sitter-java.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_java as tsj
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

JAVA_LANGUAGE = Language(tsj.language())
_parser = Parser(JAVA_LANGUAGE)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Walk a Java method body, return called method names (deduped).

    Covers method_invocation (`obj.method()`, `method()`) and
    object_creation_expression (`new Foo()`).
    """
    calls: list[str] = []
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type == "method_invocation":
            name_node = n.child_by_field_name("name")
            if name_node:
                name = _ident(name_node, src)
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        elif n.type == "object_creation_expression":
            type_node = n.child_by_field_name("type")
            if type_node:
                name = _ident(type_node, src)
                # Strip package: java.util.HashMap -> HashMap
                if "." in name:
                    name = name.split(".")[-1]
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


@register_parser(".java")
class JavaParser(BaseParser):
    """Tree-sitter parser for Java source files."""

    lang = "java"
    extensions = [".java"]
    extracts = ["classes", "interfaces", "methods", "imports", "calls"]
    description = "Java source files (.java)"
    tree_sitter_lang = "java"

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

        def _emit_class(decl: Node, kind: str) -> None:
            name_node = decl.child_by_field_name("name")
            if not name_node:
                return
            name = _ident(name_node, src)
            bases: list[str] = []
            superclass = decl.child_by_field_name("superclass")
            if superclass:
                # superclass node like `extends Foo`
                for child in superclass.children:
                    if child.type in ("identifier", "type_identifier", "scoped_type_identifier"):
                        bases.append(_ident(child, src))
            interfaces = decl.child_by_field_name("interfaces")
            if interfaces:
                for child in interfaces.children:
                    if child.type == "type_list":
                        for t in child.children:
                            if t.type in ("identifier", "type_identifier", "scoped_type_identifier"):
                                bases.append(_ident(t, src))
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
                    elif child.type == "class_declaration":
                        _emit_class(child, "class")  # nested class
                    elif child.type == "interface_declaration":
                        _emit_class(child, "interface")

        def _emit_import(decl: Node) -> None:
            # Skip the `import` keyword; the path is the scoped_identifier child.
            for child in decl.children:
                if child.type in ("scoped_identifier", "identifier"):
                    mod = _text(child, src)
                    if mod:
                        index.imports.append(ImportRef(source_module=mod, symbols=[]))
                    return

        for node in root.children:
            if node.type == "import_declaration":
                _emit_import(node)
            elif node.type == "class_declaration":
                _emit_class(node, "class")
            elif node.type == "interface_declaration":
                _emit_class(node, "interface")
            elif node.type == "enum_declaration":
                _emit_class(node, "enum")

        return index
