# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
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
from .base import (
    BaseParser,
    Bindings,
    ClassDef,
    FileIndex,
    ImportRef,
    SymbolDef,
    bind_calls,
    typed_receivers,
)

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


def _import_bindings(node: Node, src: bytes) -> Bindings:
    """The names an import statement binds, as {local: (module, symbol)}.

    ``import a.b`` binds ``a`` to module "a"; ``import a.b as c`` binds ``c``
    to module "a.b"; ``from m import s as t`` binds ``t`` to ("m", "s").
    Relative modules keep their dots (".", "..pkg"). ``from m import *``
    is kept under the key "*m" as ("m", "*"), see bind_calls.
    """
    out: Bindings = {}
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                root = _text(child, src).split(".", 1)[0]
                out[root] = (root, "")
            elif child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is not None and alias is not None:
                    out[_ident(alias, src)] = (_text(name, src), "")
    elif node.type == "import_from_statement":
        mod_node = node.child_by_field_name("module_name")
        module = _text(mod_node, src).strip() if mod_node else ""
        for child in node.children:
            if mod_node is not None and child.start_byte == mod_node.start_byte:
                continue
            if child.type == "dotted_name":
                sym = _ident(child, src)
                out[sym] = (module, sym)
            elif child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is not None and alias is not None:
                    out[_ident(alias, src)] = (module, _ident(name, src))
            elif child.type == "wildcard_import":
                out[f"*{module}"] = (module, "*")
    return out


def _receiver(obj: Node, src: bytes) -> str:
    """The receiver of an attribute call as CallRef.receiver describes it."""
    if obj.type == "identifier":
        text = _ident(obj, src)
        return "self" if text in ("self", "cls") else text
    if obj.type == "attribute":
        parts: list[str] = []
        cur: Node | None = obj
        while cur is not None and cur.type == "attribute":
            attr = cur.child_by_field_name("attribute")
            if attr is None:
                return "?"
            parts.append(_ident(attr, src))
            cur = cur.child_by_field_name("object")
        if cur is None or cur.type != "identifier":
            return "?"
        parts.append(_ident(cur, src))
        return ".".join(reversed(parts))
    if obj.type == "call":
        fn = obj.child_by_field_name("function")
        if fn is not None and fn.type == "identifier":
            name = _ident(fn, src)
            # super().f() calls the parent; Foo(...).f() an instance of Foo.
            return "super" if name == "super" else f"{name}()"
    return "?"


# Annotations that type nothing a call can be resolved on.
_UNTYPED = frozenset(
    {
        *("Any", "object", "None", "typing.Any", "str", "bytes", "int", "float"),
        *("bool", "dict", "list", "set", "tuple", "frozenset", "type"),
    }
)
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")


def _type_name(node: Node | None, src: bytes) -> str:
    """The class an annotation names: ``T``, ``m.T``, ``"T"``, ``Optional[T]``
    and ``T | None`` give T; anything else (a container, a union of classes,
    a callable) gives ""."""
    if node is None:
        return ""
    kind = node.type
    if kind == "type":
        named = [c for c in node.children if c.is_named]
        return _type_name(named[0], src) if len(named) == 1 else ""
    if kind in ("identifier", "attribute"):
        text = _text(node, src)
        ok = _DOTTED.fullmatch(text) and text not in _UNTYPED
        return text if ok else ""
    if kind == "string":
        text = _text(node, src).strip("\"'").strip()
        return text if _DOTTED.fullmatch(text) and text not in _UNTYPED else ""
    if kind in ("generic_type", "subscript"):
        head = node.children[0] if node.children else None
        if head is None or _text(head, src).rsplit(".", 1)[-1] != "Optional":
            return ""
        args = [
            c
            for p in node.children[1:]
            for c in (p.children if p.type == "type_parameter" else [p])
            if c.is_named
        ]
        return _type_name(args[0], src) if len(args) == 1 else ""
    if kind in ("binary_operator", "union_type"):
        sides = [c for c in node.children if c.is_named and c.type != "none"]
        sides = [s for s in sides if _text(s, src) != "None"]
        return _type_name(sides[0], src) if len(sides) == 1 else ""
    return ""


def _self_attr(node: Node, src: bytes) -> str:
    """``x`` for a ``self.x`` node, else ""."""
    if node.type != "attribute":
        return ""
    obj = node.child_by_field_name("object")
    attr = node.child_by_field_name("attribute")
    if obj is None or attr is None or obj.type != "identifier":
        return ""
    return _ident(attr, src) if _ident(obj, src) == "self" else ""


def _param_types(fn_node: Node, src: bytes) -> dict[str, str]:
    """The annotated parameters of a function, as {name: class}."""
    out: dict[str, str] = {}
    params = fn_node.child_by_field_name("parameters")
    for p in params.children if params is not None else ():
        if p.type not in ("typed_parameter", "typed_default_parameter"):
            continue
        name = p.child_by_field_name("name")
        if name is None:
            name = next((c for c in p.children if c.type == "identifier"), None)
        if name is not None:
            out[_ident(name, src)] = _type_name(p.child_by_field_name("type"), src)
    return out


def _body_imports(body: Node, src: bytes) -> Bindings:
    """The import bindings made anywhere inside a function body."""
    out: Bindings = {}

    def walk(n: Node) -> None:
        if n.type in ("import_statement", "import_from_statement"):
            out.update(_import_bindings(n, src))
            return
        for child in n.children:
            walk(child)

    walk(body)
    return out


# Suffix of the binding key a class attribute's type gets when its module is
# imported inside a method (``from google.cloud import kms`` in __init__,
# then ``self._client = kms.Client()``): the other methods see no import of
# ``kms``, so the type is written ``kms#.Client`` and ``kms#`` is bound for
# them. "#" is never part of an identifier, so no local name can clash.
_LOCAL_IMPORT_MARK = "#"


def _attr_types(body: Node, src: bytes) -> tuple[dict[str, str], Bindings]:
    """The class of each instance attribute a class body types without doubt,
    and the bindings those types need in every method of the class.

    Read from class-level annotations (``x: T``) and, in every method, from
    ``self.x: T = ...``, ``self.x = T(...)`` (a capitalised callee) and
    ``self.x = param`` where ``param`` is annotated. An attribute assigned
    anything else (other than None), or two different classes, stays untyped.
    A type whose module the method imports in its own body is written with
    _LOCAL_IMPORT_MARK and its binding returned.
    """
    seen: dict[str, set[str]] = {}
    extra: Bindings = {}

    def note(attr: str, cls: str) -> None:
        seen.setdefault(attr, set()).add(cls or "?")

    def walk(n: Node, params: dict[str, str], local: Bindings) -> None:
        if n.type == "class_definition":
            return
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            attr = _self_attr(left, src) if left is not None else ""
            if attr:
                ann = n.child_by_field_name("type")
                right = n.child_by_field_name("right")
                if ann is not None:
                    note(attr, _type_name(ann, src))
                elif right is None or right.type == "none":
                    pass
                elif right.type == "call":
                    fn = right.child_by_field_name("function")
                    name = _text(fn, src) if fn is not None else ""
                    last = name.rsplit(".", 1)[-1]
                    typed = _DOTTED.fullmatch(name) and last[:1].isupper()
                    head, dot, rest = name.partition(".")
                    if typed and dot and head in local:
                        extra[head + _LOCAL_IMPORT_MARK] = local[head]
                        name = f"{head}{_LOCAL_IMPORT_MARK}.{rest}"
                    note(attr, name if typed else "")
                elif right.type == "identifier":
                    note(attr, params.get(_ident(right, src), ""))
                else:
                    note(attr, "")
            elif left is not None and left.type == "pattern_list":
                for item in left.children:
                    if _self_attr(item, src):
                        note(_self_attr(item, src), "")
        for child in n.children:
            walk(child, params, local)

    for child in body.children:
        node = child
        if node.type == "decorated_definition":
            node = _first_child_of_type(node, "function_definition") or node
        if node.type == "function_definition":
            fn_body = node.child_by_field_name("body")
            if fn_body is not None:
                walk(fn_body, _param_types(node, src), _body_imports(fn_body, src))
        elif node.type == "expression_statement":
            for stmt in node.children:
                if stmt.type != "assignment":
                    continue
                left = stmt.child_by_field_name("left")
                ann = stmt.child_by_field_name("type")
                if left is not None and left.type == "identifier" and ann is not None:
                    note(_ident(left, src), _type_name(ann, src))
    types = {
        attr: next(iter(classes))
        for attr, classes in seen.items()
        if len(classes) == 1 and "?" not in classes
    }
    return types, extra


def _identifiers(node: Node, src: bytes) -> list[str]:
    """Every identifier in ``node``'s subtree (``node`` included)."""
    if node.type == "identifier":
        return [_ident(node, src)]
    return [name for child in node.children for name in _identifiers(child, src)]


def _local_types(fn_node: Node, src: bytes, returns: dict[str, str]) -> dict[str, str]:
    """The class of each local variable a function types without doubt.

    A name bound only by ``x = C(...)`` (a capitalised callee), ``x: C = ...``
    or ``x = f(...)`` / ``x = await f(...)`` where ``f`` is a function of the
    file annotated ``-> C`` (``returns``) has type C. A name bound any other
    way too (a parameter, a loop or ``with`` target, a tuple unpacking, a
    nested function's parameter, an import, a walrus, two different classes)
    stays untyped: the call on it is resolved as before.
    """
    seen: dict[str, set[str]] = {}

    def note(name: str, cls: str) -> None:
        seen.setdefault(name, set()).add(cls or "?")

    def unknown(node: Node | None) -> None:
        if node is not None:
            for name in _identifiers(node, src):
                note(name, "")

    def value_type(right: Node) -> str:
        if right.type == "await":
            inner = [c for c in right.children if c.is_named]
            right = inner[0] if len(inner) == 1 else right
        if right.type != "call":
            return ""
        fn = right.child_by_field_name("function")
        name = _text(fn, src) if fn is not None else ""
        if not _DOTTED.fullmatch(name):
            return ""
        if name.rsplit(".", 1)[-1][:1].isupper():
            return name
        return returns.get(name, "") if fn.type == "identifier" else ""

    def walk(n: Node, top: bool) -> None:
        kind = n.type
        if kind == "assignment":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            ann = n.child_by_field_name("type")
            if left is not None and left.type == "identifier":
                if ann is not None:
                    note(_ident(left, src), _type_name(ann, src))
                elif right is not None:
                    note(_ident(left, src), value_type(right))
            else:
                unknown(left)
        elif kind in ("augmented_assignment", "for_statement", "for_in_clause"):
            unknown(n.child_by_field_name("left"))
        elif kind == "as_pattern":
            unknown(n.child_by_field_name("alias"))
        elif kind == "named_expression":
            unknown(n.child_by_field_name("name"))
        elif kind in ("global_statement", "nonlocal_statement", "case_pattern"):
            unknown(n)
        elif kind in ("import_statement", "import_from_statement"):
            for name in _import_bindings(n, src):
                note(name, "")
        elif kind == "class_definition":
            # A nested class: its body binds class attributes, not locals.
            unknown(n.child_by_field_name("name"))
            return
        elif kind == "function_definition" and not top:
            unknown(n.child_by_field_name("name"))
            unknown(n.child_by_field_name("parameters"))
        elif kind == "lambda":
            unknown(n.child_by_field_name("parameters"))
        if kind == "function_definition" and top:
            unknown(n.child_by_field_name("parameters"))
            body = n.child_by_field_name("body")
            if body is not None:
                walk(body, False)
            return
        for child in n.children:
            walk(child, False)

    walk(fn_node, True)
    return {
        name: next(iter(classes))
        for name, classes in seen.items()
        if len(classes) == 1 and "?" not in classes and name not in ("self", "cls")
    }


def _local_receivers(
    raw: list[tuple[str, str]], types: dict[str, str]
) -> list[tuple[str, str]]:
    """Rewrite a call on a typed local variable (``x.f()``, see _local_types)
    as a call on its class, in the shapes typed_receivers uses."""
    if not types:
        return raw
    out: list[tuple[str, str]] = []
    for name, receiver in raw:
        cls = types.get(receiver)
        if cls:
            receiver = cls if "." in cls else f"{cls}()"
        out.append((name, receiver))
    return out


def _collect_calls(
    node: Node, src: bytes
) -> tuple[list[str], list[tuple[str, str]], Bindings]:
    """Recursively collect the calls within a function body.

    Returns the called names (deduplicated, in order), the raw
    (name, receiver) pairs, and the imports made inside the body.
    """
    calls: list[str] = []
    raw: list[tuple[str, str]] = []
    local: Bindings = {}
    visited: set[int] = set()

    def walk(n: Node) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        if n.type in ("import_statement", "import_from_statement"):
            local.update(_import_bindings(n, src))
        elif n.type == "call":
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
                    if func_node.type == "identifier":
                        raw.append((name, ""))
                    elif func_node.type == "attribute":
                        obj = func_node.child_by_field_name("object")
                        raw.append((name, _receiver(obj, src) if obj else "?"))
                    else:
                        raw.append((name, "?"))
        for child in n.children:
            walk(child)

    walk(node)
    return list(dict.fromkeys(calls)), raw, local


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
        # Module-level import bindings, and per function its raw calls and
        # the imports made in its body: calls are bound once the whole file
        # is read, since an import may follow the function that uses it.
        module_bindings: Bindings = {}
        pending: list[tuple[SymbolDef, Node, list[tuple[str, str]], Bindings]] = []
        # Per class name, the classes of its typed instance attributes and
        # the bindings of the modules some of them come from.
        attr_types: dict[str, dict[str, str]] = {}
        attr_bindings: dict[str, Bindings] = {}
        # Per module-level function name, the class its return annotates.
        returns: dict[str, str] = {}

        def _visit(node: Node, current_class: str | None = None) -> None:
            if node.type in ("import_statement", "import_from_statement"):
                module_bindings.update(_import_bindings(node, src))
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

                # Relative imports: `from . import x` and `from .. import y`
                # have no module_name field; the leading dots live in a
                # relative_import child. Reconstruct so the resolver sees
                # ".x" / "..y" instead of "" + a sibling-name symbol list.
                if not module:
                    for child in node.children:
                        if child.type == "relative_import":
                            prefix = _text(child, src).strip()
                            if prefix:
                                module = prefix
                            break

                symbols = [
                    _text(c, src)
                    for c in node.children
                    if c.type == "dotted_name" and c != mod_node
                ]

                # When the import is purely relative + dotted names
                # (`from . import x, y`), treat each symbol as a sibling
                # module so the resolver can wire one edge per target.
                if module in (".", "..") and symbols:
                    for sym in symbols:
                        index.imports.append(
                            ImportRef(source_module=module + sym, symbols=[])
                        )
                else:
                    index.imports.append(
                        ImportRef(source_module=module, symbols=symbols)
                    )

            # --- class ---
            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                body_node = node.child_by_field_name("body")
                name = _ident(name_node, src) if name_node else "?"
                bases: list[str] = []
                args_node = node.child_by_field_name("superclasses")
                if args_node:
                    for arg in args_node.children:
                        # Base[T] and mod.Base[int, str] subclass Base and
                        # mod.Base: keep the subscripted name, drop the args.
                        while arg.type == "subscript":
                            value = arg.child_by_field_name("value")
                            if value is None:
                                break
                            arg = value
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
                if body_node:
                    # Two classes of one name in a file: neither is typed.
                    types, extra = _attr_types(body_node, src)
                    attr_types[name] = {} if name in attr_types else types
                    attr_bindings[name] = {} if name in attr_bindings else extra
                # Recurse into class body with class context
                if body_node:
                    for child in body_node.children:
                        _visit(child, current_class=name)
                return  # already recursed

            # --- function / method ---
            elif node.type in ("function_definition", "decorated_definition"):
                fn_node = (
                    node
                    if node.type == "function_definition"
                    else _first_child_of_type(node, "function_definition")
                )
                if fn_node is None:
                    for child in node.children:
                        _visit(child, current_class)
                    return

                name_node = fn_node.child_by_field_name("name")
                body_node = fn_node.child_by_field_name("body")
                name = _ident(name_node, src) if name_node else "?"
                doc = _extract_docstring(body_node, src) if body_node else ""
                calls, raw_calls, local_bindings = _collect_calls(fn_node, src)
                fn_id = (
                    f"{path_str}::{current_class}.{name}"
                    if current_class
                    else f"{path_str}::{name}"
                )
                kind = "method" if current_class else "function"

                fn = SymbolDef(
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
                index.functions.append(fn)
                pending.append((fn, fn_node, raw_calls, local_bindings))
                if not current_class:
                    ret = _type_name(fn_node.child_by_field_name("return_type"), src)
                    # Two functions of one name: neither return is trusted.
                    returns[name] = "" if name in returns else ret
                return

            # Default: recurse
            for child in node.children:
                _visit(child, current_class)

        for child in root.children:
            _visit(child)

        returns_known = {k: v for k, v in returns.items() if v}
        for fn, fn_node, raw_calls, local_bindings in pending:
            bindings = {**module_bindings, **local_bindings}
            if fn.class_name:
                raw_calls = typed_receivers(
                    raw_calls, attr_types.get(fn.class_name, {}), "self"
                )
                bindings.update(attr_bindings.get(fn.class_name, {}))
            raw_calls = _local_receivers(
                raw_calls, _local_types(fn_node, src, returns_known)
            )
            fn.call_refs = bind_calls(raw_calls, bindings)
        return index
