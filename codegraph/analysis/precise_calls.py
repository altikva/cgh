# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Opt-in precise CALLS resolver for Python (proof of concept).
#              Uses jedi (the static engine python-lsp-server wraps) to do
#              goto-definition on every call site in a file, then maps each
#              resolved definition to codegraph's Function id scheme
#              ("{file}::{name}" or "{file}::{Class}.{method}"). Only targets
#              that resolve to a file INSIDE repo_root are kept; stdlib and
#              site-packages are dropped. jedi is imported lazily and every
#              failure degrades to an empty result, so the core install never
#              depends on it. The public shape (resolve_calls_for_file) is the
#              seam a real LSP backend could replace later.

from __future__ import annotations

import sys
from pathlib import Path


def _import_jedi():
    """Import jedi + parso lazily, then restore the recursion limit.

    parso lowers sys.recursionlimit to 3000 on import, which would silently
    undo the indexer's raised limit (it bumps to 10_000 so tree-sitter walks
    on deeply nested ASTs don't crash). Re-assert the higher value after the
    import so enabling this opt-in feature never weakens the core guard.
    Returns (jedi, parso) or None if either is missing.
    """
    prior = sys.getrecursionlimit()
    try:
        import jedi
        import parso
    except Exception:
        return None
    if sys.getrecursionlimit() < prior:
        sys.setrecursionlimit(prior)
    return jedi, parso


# Hard cap on call sites resolved per file. goto() is the expensive step;
# a pathological generated file should not stall a scan. Past the cap we
# stop and return what we have.
_MAX_CALL_SITES = 2000


def jedi_available() -> bool:
    """True when jedi can be imported. Cheap, swallows any import error."""
    return _import_jedi() is not None


def _enclosing_caller_id(leaf, file_path: str) -> str | None:
    """Walk up the parso tree from a call leaf to the function that contains
    it, and build that function's graph Function id.

    Mirrors the parser's qualname scheme: a method gets
    "{file}::{Class}.{method}", a plain function "{file}::{name}". Only the
    nearest enclosing class is used (the parser does not model deeper
    nesting either). Returns None when the call sits at module level.
    """
    funcdef = None
    node = leaf.parent
    while node is not None:
        if getattr(node, "type", None) == "funcdef":
            funcdef = node
            break
        node = node.parent
    if funcdef is None:
        return None

    fn_name = funcdef.name.value

    # Nearest enclosing class, if any, for the Class.method form.
    class_name = None
    node = funcdef.parent
    while node is not None:
        if getattr(node, "type", None) == "classdef":
            class_name = node.name.value
            break
        if getattr(node, "type", None) == "funcdef":
            # A function nested inside another function: stop, the parser
            # would not attach a class context here.
            break
        node = node.parent

    if class_name:
        return f"{file_path}::{class_name}.{fn_name}"
    return f"{file_path}::{fn_name}"


def _iter_call_leaves(node):
    """Yield the name leaf being called for every call expression in a parso
    tree. For "obj.method()" this yields the "method" leaf; for "foo()" the
    "foo" leaf.
    """
    node_type = getattr(node, "type", None)
    if node_type in ("atom_expr", "power"):
        children = node.children
        for i, ch in enumerate(children):
            if (
                getattr(ch, "type", None) == "trailer"
                and ch.children
                and getattr(ch.children[0], "type", None) == "operator"
                and ch.children[0].value == "("
            ):
                callee = children[i - 1] if i > 0 else children[0]
                leaf = callee
                while getattr(leaf, "children", None):
                    leaf = leaf.children[-1]
                if getattr(leaf, "type", None) == "name":
                    yield leaf
    for child in getattr(node, "children", []) or []:
        yield from _iter_call_leaves(child)


def _target_id(
    definition, repo_root: Path, repo_root_real: Path
) -> tuple[str, str] | None:
    """Map a jedi Definition to a (target_file, target_id) pair, or None if it
    does not resolve to a function/method defined in a file inside the repo.

    repo_root is the path the indexer was called with (used to rebuild the
    target file path in the SAME form the indexer stored). repo_root_real is
    its symlink-resolved form, used only for the containment check, since
    jedi reports module_path with symlinks resolved (e.g. /tmp -> /private/tmp
    on macOS). Rebuilding from repo_root keeps the id byte-identical to the
    Function node the indexer wrote.
    """
    mod_path = definition.module_path
    if mod_path is None:
        return None
    try:
        target_real = Path(mod_path).resolve()
    except Exception:
        return None

    # Keep only definitions inside the repo (drop stdlib / site-packages).
    try:
        rel = target_real.relative_to(repo_root_real)
    except ValueError:
        return None

    if definition.type not in ("function", "method"):
        return None

    name = definition.name
    if not name:
        return None

    # Class context comes from the definition's parent. jedi reports a
    # method's parent as the owning class.
    class_name = None
    try:
        parent = definition.parent()
    except Exception:
        parent = None
    if parent is not None and getattr(parent, "type", None) == "class":
        class_name = parent.name

    # Rebuild the file path from the indexer's (possibly unresolved) repo_root
    # so the id matches the stored Function node exactly.
    file_str = str(repo_root / rel)
    if class_name:
        return (file_str, f"{file_str}::{class_name}.{name}")
    return (file_str, f"{file_str}::{name}")


def resolve_calls_for_file(
    file_path: str | Path, repo_root: str | Path
) -> list[tuple[str, str, str]]:
    """Resolve every Python call site in ``file_path`` to its definition.

    Returns a list of (caller_id, target_file, target_id) tuples, where
    caller_id and target_id are graph Function ids and target_file is the
    absolute path of the file that defines the callee. Only callees that
    resolve INSIDE repo_root are returned.

    Never raises: if jedi is missing, the file is unreadable, or jedi errors
    on a node, the offending item is skipped and resolution continues. A
    total failure yields an empty list, which the indexer treats as "fall
    back to the name-matched resolver".
    """
    mods = _import_jedi()
    if mods is None:
        return []
    jedi, parso = mods

    file_path = Path(file_path)
    repo_root = Path(repo_root)
    repo_root_real = repo_root.resolve()

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        project = jedi.Project(str(repo_root_real))
        script = jedi.Script(code=source, path=str(file_path), project=project)
        tree = parso.parse(source)
    except Exception:
        return []

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    count = 0
    for leaf in _iter_call_leaves(tree):
        if count >= _MAX_CALL_SITES:
            break
        count += 1

        caller_id = _enclosing_caller_id(leaf, str(file_path))
        if caller_id is None:
            # Call at module level: no Function node to anchor the edge.
            continue

        line, column = leaf.start_pos
        try:
            definitions = script.goto(line, column, follow_imports=True)
        except Exception:
            continue

        for definition in definitions:
            try:
                resolved = _target_id(definition, repo_root, repo_root_real)
            except Exception:
                resolved = None
            if resolved is None:
                continue
            target_file, target_id = resolved
            # Drop self-recursion noise the name matcher never emitted either.
            if target_id == caller_id:
                continue
            key = (caller_id, target_id)
            if key in seen:
                continue
            seen.add(key)
            out.append((caller_id, target_file, target_id))

    return out
