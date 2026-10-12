# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared types across federated scopes, deduced at query time.
#              Federation never links one repo's graph to another's, so a
#              backend Pydantic model and the frontend TypeScript interface
#              that mirrors it stay unrelated. shared_types_report finds
#              types (classes, interfaces, object type aliases) defined
#              under the same name in two scopes whose field sets agree,
#              and reports each pair as {name, scopes, matched_fields,
#              confidence, deduced: true}. Nothing is stored: the parent is
#              read through its own connection, each child through a
#              read-only open, and fields are read from the source files.

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Languages whose type declarations we read fields from.
_PY_EXTS = (".py", ".pyi")
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# A type name shared by many unrelated classes (Config, Meta) would compare
# every pair; past this many definitions in one scope the name is skipped.
_MAX_DEFS_PER_NAME = 20
# Names compared at most, so a huge workspace cannot turn the view into a
# full scan of every file.
_MAX_NAMES = 2000
_DEFAULT_LIMIT = 100

NOTE = (
    "Deduced, not indexed: a pair is two types with the same name in two "
    "scopes whose field names agree (snake_case and camelCase compare "
    "equal). confidence is matched fields over all fields of the pair. No "
    "cross-repo edge is stored; read both definitions before relying on it."
)


def type_candidates(conn: Any) -> list[dict[str, Any]]:
    """The Class nodes of one scope that may declare a shape: Python, and
    TypeScript / JavaScript (classes, interfaces, object type aliases)."""
    out: list[dict[str, Any]] = []
    for r in conn.find_nodes(
        "Class", return_fields=["name", "file_path", "start_line", "end_line"]
    ):
        fp = r.get("file_path") or ""
        if r.get("name") and fp.endswith(_PY_EXTS + _TS_EXTS):
            out.append(r)
    return out


def _norm(field: str) -> str:
    """snake_case and camelCase spellings of one field compare equal."""
    return field.replace("_", "").lower()


def _python_fields(text: str) -> list[str]:
    """The annotated attributes a Python class body declares (Pydantic
    models, dataclasses, TypedDicts, attrs), private names left out."""
    try:
        tree = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        return []
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if cls is None:
        return []
    out: list[str] = []
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and not stmt.target.id.startswith("_")
            and stmt.target.id != "model_config"
        ):
            out.append(stmt.target.id)
    return out


def _ts_fields(text: str, tsx: bool) -> list[str]:
    """The properties a TypeScript interface, object type alias or class
    declares (property signatures and field definitions), methods left out."""
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    lang = tsts.language_tsx() if tsx else tsts.language_typescript()
    src = text.encode("utf-8")
    root = Parser(Language(lang)).parse(src).root_node
    body = None
    stack = [root]
    while stack and body is None:
        node = stack.pop(0)
        if node.type in ("interface_body", "object_type", "class_body"):
            body = node
            break
        stack.extend(node.children)
    if body is None:
        return []
    out: list[str] = []
    for child in body.named_children:
        if child.type not in ("property_signature", "public_field_definition"):
            continue
        name = child.child_by_field_name("name")
        if name is None or name.type not in (
            "property_identifier",
            "identifier",
            "string",
        ):
            continue
        out.append(src[name.start_byte : name.end_byte].decode().strip("\"'"))
    return out


class _SourceCache:
    """Each file read once per report, and only inside its scope's root."""

    def __init__(self) -> None:
        self._lines: dict[str, list[str] | None] = {}

    def lines(self, file_path: str, root: Path) -> list[str] | None:
        if file_path in self._lines:
            return self._lines[file_path]
        lines: list[str] | None = None
        try:
            path = Path(file_path).resolve()
            # A path read from a graph is still confined to its own repo.
            if path.is_relative_to(root.resolve()) and path.is_file():
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = None
        self._lines[file_path] = lines
        return lines


def fields_of(row: dict[str, Any], root: Path, cache: _SourceCache) -> list[str]:
    """The field names of one type definition, read from its source lines."""
    fp = row.get("file_path") or ""
    lines = cache.lines(fp, root)
    if not lines:
        return []
    start = max(1, int(row.get("start_line") or 1))
    end = max(start, int(row.get("end_line") or start))
    text = "\n".join(lines[start - 1 : end])
    if fp.endswith(_PY_EXTS):
        fields = _python_fields(text)
    else:
        fields = _ts_fields(text, tsx=fp.endswith((".tsx", ".jsx")))
    return list(dict.fromkeys(fields))


def match_shared_types(
    scopes: list[tuple[str, Path, list[dict[str, Any]]]],
    name: str = "",
    min_confidence: float = 0.5,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Pair the same-named types of different scopes whose fields agree.

    ``scopes`` is ``[(scope, root, type_candidates rows), ...]``. Returns
    ``(pairs, truncated)``, best confidence first. Each pair is ``{name,
    scopes, matched_fields, confidence, deduced, definitions, only_in}``:
    ``matched_fields`` as the first scope spells them, ``definitions`` one
    ``{scope, file, line, fields}`` per side and ``only_in`` the fields each
    side has that the other lacks."""
    by_name: dict[str, dict[str, list[dict[str, Any]]]] = {}
    roots: dict[str, Path] = {}
    for scope, root, rows in scopes:
        roots[scope] = root
        for r in rows:
            if name and r["name"] != name:
                continue
            by_name.setdefault(r["name"], {}).setdefault(scope, []).append(r)
    shared = sorted(n for n, per in by_name.items() if len(per) >= 2)
    truncated = len(shared) > _MAX_NAMES
    cache = _SourceCache()
    pairs: list[dict[str, Any]] = []
    for type_name in shared[:_MAX_NAMES]:
        per = by_name[type_name]
        defs: list[tuple[str, dict[str, Any], list[str]]] = []
        for scope, rows in per.items():
            if len(rows) > _MAX_DEFS_PER_NAME:
                continue
            for r in rows:
                fields = fields_of(r, roots[scope], cache)
                if fields:
                    defs.append((scope, r, fields))
        for i, (sa, ra, fa) in enumerate(defs):
            for sb, rb, fb in defs[i + 1 :]:
                if sa == sb:
                    continue
                pair = _compare(type_name, (sa, ra, fa), (sb, rb, fb))
                if pair is not None and pair["confidence"] >= min_confidence:
                    pairs.append(pair)
    pairs.sort(key=lambda p: (-p["confidence"], p["name"], p["scopes"]))
    if limit > 0 and len(pairs) > limit:
        return pairs[:limit], True
    return pairs, truncated


def _compare(
    type_name: str,
    a: tuple[str, dict[str, Any], list[str]],
    b: tuple[str, dict[str, Any], list[str]],
) -> dict[str, Any] | None:
    (sa, ra, fa), (sb, rb, fb) = a, b
    na = {_norm(f): f for f in fa}
    nb = {_norm(f): f for f in fb}
    common = [k for k in na if k in nb]
    if not common:
        return None
    union = len(set(na) | set(nb))
    return {
        "name": type_name,
        "scopes": [sa, sb],
        "matched_fields": [na[k] for k in common],
        "confidence": round(len(common) / union, 2),
        "deduced": True,
        "definitions": [
            {
                "scope": sa,
                "file": ra["file_path"],
                "line": ra.get("start_line"),
                "fields": fa,
            },
            {
                "scope": sb,
                "file": rb["file_path"],
                "line": rb.get("start_line"),
                "fields": fb,
            },
        ],
        "only_in": {
            sa: [na[k] for k in na if k not in nb],
            sb: [nb[k] for k in nb if k not in na],
        },
    }


def shared_types_report(
    get_parent_conn: Callable[[], Any | None],
    repo_root: str | Path | None,
    name: str = "",
    min_confidence: float = 0.5,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The shared_types payload for the parent and its federated children.

    The parent is read through ``get_parent_conn()`` (the owner's own
    connection, or a local read-only open; None when locked), each child
    through a read-only open (for_each_child_graphdb), never written."""
    from codegraph.analysis.federation import for_each_child_graphdb

    if repo_root is None:
        return {"shared_types": [], "count": 0, "error": "no repo root"}
    root = Path(repo_root).resolve()
    scopes: list[tuple[str, Path, list[dict[str, Any]]]] = []
    warnings: list[dict[str, str]] = []
    try:
        conn = get_parent_conn()
        if conn is None:
            warnings.append({"scope": "parent", "error": "graph DB is locked"})
        else:
            scopes.append(("parent", root, type_candidates(conn)))
    except Exception as exc:
        warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})
    for s in for_each_child_graphdb(root, lambda c, _r: type_candidates(c)):
        if s.error:
            warnings.append({"scope": s.scope, "error": s.error})
            continue
        scopes.append((s.scope, s.scope_path, s.payload or []))

    pairs, truncated = match_shared_types(
        scopes, name=name, min_confidence=min_confidence, limit=limit
    )
    payload: dict[str, Any] = {
        "shared_types": pairs,
        "count": len(pairs),
        "scopes": [s for s, _r, _rows in scopes],
        "min_confidence": min_confidence,
        "truncated": truncated,
        "note": NOTE,
    }
    if len(scopes) + len(warnings) < 2:
        payload["note"] = (
            "No federated subrepo: shared types compare two scopes or more "
            "(see `cgh federate add`). " + NOTE
        )
    if warnings:
        payload["partial"] = True
        payload["warnings"] = warnings
    return payload
