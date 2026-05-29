# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Python parser plugin for codegraph.
#              Extracts functions, classes, imports, and call references
#              using tree-sitter.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_python as tsp
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import BaseParser, ClassDef, FileIndex, ImportRef, SymbolDef

PY_LANGUAGE = Language(tsp.language())
_parser = Parser(PY_LANGUAGE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    """Like _text but NFKC-normalized for identifier nodes.

    Use this anywhere the returned string will become part of a node ID
    or a name we'll match against other identifiers. Composed and
    decomposed Unicode forms collapse to the same string, so a single
    symbol doesn't fork into two nodes.
    """
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _first_child_of_type(node: Node, *types: str) -> Node | None:
    for child in node.children:
        if child.type in types:
            return child
    return None


def _extract_docstring(body_node: Node, src: bytes) -> str:
    """Return the first string literal in a block as a docstring."""
    for child in body_node.children:
        if child.type == "expression_statement":
            inner = _first_child_of_type(child, "string")
            if inner:
                raw = _text(inner, src).strip("\"' \n")
                # Collapse triple-quoted noise
                raw = re.sub(r"\s+", " ", raw)
                return raw[:300]
    return ""


def _collect_calls(node: Node, src: bytes) -> list[str]:
    """Recursively collect all called names within a function body."""
    calls: list[str] = []
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type == "call":
            func_node = n.child_by_field_name("function")
            if func_node:
                name = _ident(func_node, src)
                # Strip attribute access: "self.foo" -> "foo", "obj.method" -> "method"
                if "." in name:
                    name = name.split(".")[-1]
                # \w is Unicode-aware on Python 3 so non-ASCII identifiers
                # (CJK, accented Latin, Cyrillic) survive the filter.
                if re.match(r"^\w+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# Parser plugin
# ---------------------------------------------------------------------------


@register_parser(".py", ".pyw")
class PythonParser(BaseParser):
    """Tree-sitter parser for Python source files."""

    lang = "python"
    extensions = [".py", ".pyw"]
    extracts = ["functions", "classes", "imports", "calls", "inheritance"]
    description = "Python source files (.py, .pyw)"
    tree_sitter_lang = "python"

    def parse(self, path: Path) -> FileIndex:
        path = Path(path)
        path_str = str(path)
        src = path.read_bytes()
        tree = _parser.parse(src)
        root = tree.root_node

        index = FileIndex(path=path_str, lang=self.lang)

        def _visit(node: Node, current_class: str | None = None) -> None:
            # --- imports ---
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        index.imports.append(
                            ImportRef(
                                source_module=_text(child, src),
                                symbols=[],
                            )
                        )

            elif node.type == "import_from_statement":
                mod_node = node.child_by_field_name("module_name")
                module = _text(mod_node, src) if mod_node else ""
                symbols = [_text(c, src) for c in node.children if c.type == "dotted_name" and c != mod_node]
                index.imports.append(ImportRef(source_module=module, symbols=symbols))

            # --- class ---
            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                body_node = node.child_by_field_name("body")
                name = _ident(name_node, src) if name_node else "?"
                bases: list[str] = []
                args_node = node.child_by_field_name("superclasses")
                if args_node:
                    for arg in args_node.children:
                        if arg.type in ("identifier", "dotted_name", "attribute"):
                            bases.append(_ident(arg, src))

                doc = _extract_docstring(body_node, src) if body_node else ""
                index.classes.append(
                    ClassDef(
                        id=f"{path_str}::{name}",
                        name=name,
                        file_path=path_str,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        docstring=doc,
                        bases=bases,
                        kind="class",
                    )
                )
                # Recurse into class body with class context
                if body_node:
                    for child in body_node.children:
                        _visit(child, current_class=name)
                return  # already recursed

            # --- function / method ---
            elif node.type in ("function_definition", "decorated_definition"):
                fn_node = (
                    node if node.type == "function_definition" else _first_child_of_type(node, "function_definition")
                )
                if fn_node is None:
                    for child in node.children:
                        _visit(child, current_class)
                    return

                name_node = fn_node.child_by_field_name("name")
                body_node = fn_node.child_by_field_name("body")
                name = _ident(name_node, src) if name_node else "?"
                doc = _extract_docstring(body_node, src) if body_node else ""
                calls = _collect_calls(fn_node, src)
                fn_id = f"{path_str}::{current_class}.{name}" if current_class else f"{path_str}::{name}"
                kind = "method" if current_class else "function"

                index.functions.append(
                    SymbolDef(
                        id=fn_id,
                        name=name,
                        file_path=path_str,
                        start_line=fn_node.start_point[0] + 1,
                        end_line=fn_node.end_point[0] + 1,
                        docstring=doc,
                        class_name=current_class,
                        calls=calls,
                        kind=kind,
                    )
                )
                return

            # Default: recurse
            for child in node.children:
                _visit(child, current_class)

        for child in root.children:
            _visit(child)

        return index
