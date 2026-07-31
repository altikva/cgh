# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tree-sitter parser for TypeScript / JavaScript source files.
#              Plugin for the codegraph parser registry.

from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from . import register_parser
from .base import (
    BaseParser,
    ClassDef,
    FileIndex,
    ImportRef,
    SymbolDef,
)

_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())

_ts_parser = Parser(_TS_LANGUAGE)
_tsx_parser = Parser(_TSX_LANGUAGE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ident(node: Node, src: bytes) -> str:
    """NFKC-normalized identifier extraction. See codegraph.core.utils."""
    from codegraph.core.utils import normalize_identifier

    return normalize_identifier(_text(node, src))


def _collect_calls(node: Node, src: bytes) -> list[str]:
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
                if "." in name:
                    name = name.split(".")[-1]
                # Allow $ (JS identifier char) plus Unicode word chars.
                if re.match(r"^[\w$]+$", name, re.UNICODE):
                    calls.append(name)
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls))


def _fn_name(node: Node, src: bytes) -> str:
    """Best-effort function name extraction across declaration styles."""
    # function foo() / function* foo()
    name_node = node.child_by_field_name("name")
    if name_node:
        return _ident(name_node, src)
    # const foo = () => ...  (parent is variable_declarator)
    if node.parent and node.parent.type == "variable_declarator":
        id_node = node.parent.child_by_field_name("name")
        if id_node:
            return _ident(id_node, src)
    return "<anonymous>"


# ---------------------------------------------------------------------------
# Parser plugin
# ---------------------------------------------------------------------------


@register_parser(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
class TypeScriptParser(BaseParser):
    """Tree-sitter based parser for TypeScript and JavaScript files."""

    lang = "typescript"
    extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
    extracts = ["functions", "classes", "imports", "calls", "inheritance"]
    description = "TypeScript / JavaScript parser (tree-sitter)"
    tree_sitter_lang = "typescript"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        suffix = Path(path_str).suffix.lower()
        src = Path(path_str).read_bytes()

        parser = _tsx_parser if suffix in (".tsx", ".jsx") else _ts_parser
        lang_label = (
            "tsx" if suffix in (".tsx", ".jsx") else "javascript" if suffix in (".js", ".mjs", ".cjs") else "typescript"
        )

        tree = parser.parse(src)
        root = tree.root_node
        index = FileIndex(path=path_str, lang=lang_label)

        def _visit(node: Node, current_class: str | None = None) -> None:
            # --- import ---
            if node.type == "import_statement":
                source_node = node.child_by_field_name("source")
                module = _text(source_node, src).strip("\"'") if source_node else ""
                symbols: list[str] = []
                for child in node.children:
                    if child.type == "import_clause":
                        for sub in child.children:
                            if sub.type == "named_imports":
                                for spec in sub.children:
                                    if spec.type == "import_specifier":
                                        n = spec.child_by_field_name("name")
                                        if n:
                                            symbols.append(_ident(n, src))
                            elif sub.type == "identifier":
                                symbols.append(_ident(sub, src))
                index.imports.append(ImportRef(source_module=module, symbols=symbols))

            # --- class ---
            elif node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                name = _ident(name_node, src) if name_node else "?"
                bases: list[str] = []
                heritage = node.child_by_field_name("heritage")
                if heritage:
                    for child in heritage.children:
                        if child.type in ("identifier", "member_expression"):
                            bases.append(_ident(child, src))
                body = node.child_by_field_name("body")
                index.classes.append(
                    ClassDef(
                        id=f"{path_str}::{name}",
                        name=name,
                        file_path=path_str,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        docstring="",
                        bases=bases,
                    )
                )
                if body:
                    for child in body.children:
                        _visit(child, current_class=name)
                return

            # --- function / method / arrow ---
            elif node.type in (
                "function_declaration",
                "function",
                "arrow_function",
                "method_definition",
            ):
                name = _fn_name(node, src)
                calls = _collect_calls(node, src)
                fn_id = f"{path_str}::{current_class}.{name}" if current_class else f"{path_str}::{name}"
                kind = "method" if current_class else "arrow" if node.type == "arrow_function" else "function"
                index.functions.append(
                    SymbolDef(
                        id=fn_id,
                        name=name,
                        file_path=path_str,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        docstring="",
                        class_name=current_class,
                        calls=calls,
                        kind=kind,
                    )
                )
                return

            for child in node.children:
                _visit(child, current_class)

        for child in root.children:
            _visit(child)

        return index
