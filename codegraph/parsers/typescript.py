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
    Bindings,
    ClassDef,
    FileIndex,
    ImportRef,
    SymbolDef,
    bind_calls,
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


def _receiver(obj: Node, src: bytes) -> str:
    """The receiver of a member call as CallRef.receiver describes it."""
    if obj.type == "this":
        return "self"
    if obj.type == "super":
        return "super"
    if obj.type == "identifier":
        return _ident(obj, src)
    if obj.type == "member_expression":
        parts: list[str] = []
        cur: Node | None = obj
        while cur is not None and cur.type == "member_expression":
            prop = cur.child_by_field_name("property")
            if prop is None:
                return "?"
            parts.append(_ident(prop, src))
            cur = cur.child_by_field_name("object")
        if cur is None or cur.type not in ("identifier", "this"):
            return "?"
        parts.append(_ident(cur, src))
        return ".".join(reversed(parts))
    if obj.type == "parenthesized_expression" and obj.named_child_count == 1:
        return _receiver(obj.named_children[0], src)
    # new Foo().f() calls a method of Foo; foo().f() one of what foo returns.
    callee = None
    if obj.type == "new_expression":
        callee = obj.child_by_field_name("constructor")
    elif obj.type == "call_expression":
        callee = obj.child_by_field_name("function")
    if callee is not None and callee.type == "identifier":
        return f"{_ident(callee, src)}()"
    return "?"


def _collect_calls(node: Node, src: bytes) -> tuple[list[str], list[tuple[str, str]]]:
    """Called names (deduplicated, in order) and raw (name, receiver) pairs."""
    calls: list[str] = []
    raw: list[tuple[str, str]] = []
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
                    if fn.type == "identifier":
                        raw.append((name, ""))
                    elif fn.type == "member_expression":
                        obj = fn.child_by_field_name("object")
                        raw.append((name, _receiver(obj, src) if obj else "?"))
                    else:
                        raw.append((name, "?"))
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls)), raw


def _import_bindings(node: Node, module: str, src: bytes) -> Bindings:
    """The names an import statement binds, as {local: (module, symbol)}:
    a default import gives symbol "default", a namespace import ""."""
    out: Bindings = {}
    for child in node.children:
        if child.type != "import_clause":
            continue
        for sub in child.children:
            if sub.type == "identifier":
                out[_ident(sub, src)] = (module, "default")
            elif sub.type == "namespace_import":
                for leaf in sub.children:
                    if leaf.type == "identifier":
                        out[_ident(leaf, src)] = (module, "")
            elif sub.type == "named_imports":
                for spec in sub.children:
                    if spec.type != "import_specifier":
                        continue
                    name = spec.child_by_field_name("name")
                    alias = spec.child_by_field_name("alias")
                    if name is not None:
                        local = alias if alias is not None else name
                        out[_ident(local, src)] = (module, _ident(name, src))
    return out


def _class_bases(node: Node, src: bytes) -> list[str]:
    """The names a class extends, then the ones it implements, in source order.

    The grammar puts them in a positional ``class_heritage`` child, not a
    field. Type arguments are dropped (``Base<T>`` gives ``Base``) and a
    qualified base keeps its dots (``ns.Base``), as Python and Java store them.
    An expression base such as ``mixin(Base)`` names no class and is skipped.
    Implemented interfaces are recorded as bases, as Java and C# do.
    """
    bases: list[str] = []
    for heritage in node.children:
        if heritage.type != "class_heritage":
            continue
        for clause in heritage.children:
            if clause.type == "extends_clause":
                value = clause.child_by_field_name("value")
                if value is not None and value.type in (
                    "identifier",
                    "member_expression",
                ):
                    bases.append(_ident(value, src))
            elif clause.type == "implements_clause":
                for t in clause.named_children:
                    if t.type == "generic_type":
                        t = t.child_by_field_name("name") or t
                    if t.type in ("type_identifier", "nested_type_identifier"):
                        bases.append(_ident(t, src))
    return bases


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
            "tsx"
            if suffix in (".tsx", ".jsx")
            else "javascript"
            if suffix in (".js", ".mjs", ".cjs")
            else "typescript"
        )

        tree = parser.parse(src)
        root = tree.root_node
        index = FileIndex(path=path_str, lang=lang_label)
        bindings: Bindings = {}
        pending: list[tuple[SymbolDef, list[tuple[str, str]]]] = []

        def _visit(node: Node, current_class: str | None = None) -> None:
            # --- import ---
            if node.type == "import_statement":
                source_node = node.child_by_field_name("source")
                module = _text(source_node, src).strip("\"'") if source_node else ""
                bindings.update(_import_bindings(node, module, src))
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
                bases = _class_bases(node, src)
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
                calls, raw_calls = _collect_calls(node, src)
                fn_id = (
                    f"{path_str}::{current_class}.{name}"
                    if current_class
                    else f"{path_str}::{name}"
                )
                kind = (
                    "method"
                    if current_class
                    else "arrow"
                    if node.type == "arrow_function"
                    else "function"
                )
                fn = SymbolDef(
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
                index.functions.append(fn)
                pending.append((fn, raw_calls))
                return

            for child in node.children:
                _visit(child, current_class)

        for child in root.children:
            _visit(child)

        for fn, raw_calls in pending:
            fn.call_refs = bind_calls(raw_calls, bindings)
        return index
