# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Repo-relative paths in tool answers. The repo prefix repeated
#              on every row was about an eighth of an answer's bytes, more on
#              a long checkout path. relative_answer rewrites every string of
#              a JSON-object answer that starts with a repo root, as a value
#              or a key: a row tagged with a federated child's `scope` is made
#              relative to that child's root, any other to the project root.
#              The answer then carries the roots it stripped, once: `root` for
#              the project and `roots` ({scope: root}) for the children, so
#              root + "/" + path is the absolute path a Read needs. A path
#              under no root, or a row whose scope names no known repo, stays
#              absolute. An answer with nothing to rewrite is returned as is.

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

PARENT_SCOPE = "parent"


def _prefixes(root: Path) -> tuple[str, ...]:
    """The spellings of ``root`` followed by a separator a stored path can
    start with: as given and resolved (a symlinked tmp dir), with ``/`` and
    the platform separator."""
    out: list[str] = []
    for spelled in (str(root), str(root.resolve())):
        for sep in ("/", os.sep):
            prefix = spelled.rstrip("/\\") + sep
            if prefix not in out:
                out.append(prefix)
    # Longest first, so a resolved spelling that extends another one wins.
    return tuple(sorted(out, key=len, reverse=True))


class _Relativizer:
    def __init__(self, parent: Path, children: Callable[[], dict[str, Path]]):
        self._parent = parent
        self._children_fn = children
        self._children: dict[str, Path] | None = None
        self._prefixes: dict[str, tuple[str, ...]] = {PARENT_SCOPE: _prefixes(parent)}
        # Scope -> the root its paths were made relative to.
        self.used: dict[str, str] = {}

    def _root_of(self, scope: str) -> Path | None:
        if scope == PARENT_SCOPE:
            return self._parent
        if self._children is None:
            try:
                self._children = dict(self._children_fn())
            except Exception:
                self._children = {}
        return self._children.get(scope)

    def _scope_prefixes(self, scope: str) -> tuple[str, ...]:
        if scope not in self._prefixes:
            root = self._root_of(scope)
            self._prefixes[scope] = _prefixes(root) if root else ()
        return self._prefixes[scope]

    def text(self, value: str, scope: str) -> str:
        for prefix in self._scope_prefixes(scope):
            if value.startswith(prefix) and len(value) > len(prefix):
                root = self._root_of(scope)
                self.used[scope] = str(root)
                return value[len(prefix) :]
        return value

    def walk(self, node: Any, scope: str) -> Any:
        if isinstance(node, str):
            return (
                self.text(node, scope)
                if node[:1] in ("/", "\\") or ":" in node[:3]
                else node
            )
        if isinstance(node, list):
            return [self.walk(item, scope) for item in node]
        if isinstance(node, dict):
            tag = node.get("scope")
            if isinstance(tag, str) and tag:
                scope = tag
            return {
                (self.walk(k, scope) if isinstance(k, str) else k): self.walk(v, scope)
                for k, v in node.items()
            }
        return node


def relative_answer(
    payload: dict[str, Any],
    parent: Path,
    children: Callable[[], dict[str, Path]],
) -> dict[str, Any] | None:
    """``payload`` with its paths made relative to the root of the repo each
    row comes from, plus ``root`` / ``roots``; None when no path was under a
    root. ``children`` returns {scope: root} of the federated children and
    is only called when a row names a scope other than the parent."""
    rel = _Relativizer(parent, children)
    out = {
        k: rel.walk(v, PARENT_SCOPE)
        for k, v in payload.items()
        if k not in ("root", "roots")
    }
    if not rel.used:
        return None
    for key in ("root", "roots"):
        if key in payload:
            out[key] = payload[key]
    parent_root = rel.used.pop(PARENT_SCOPE, None)
    if parent_root is not None:
        out.setdefault("root", parent_root)
    if rel.used:
        roots = out.get("roots") if isinstance(out.get("roots"), dict) else {}
        out["roots"] = {**roots, **rel.used}
    return out


def child_roots(parent: Path) -> dict[str, Path]:
    """{scope: root} of the federated children of ``parent``, named like the
    `scope` field of federated rows."""
    from codegraph.analysis.federation import resolve_children

    return {child.name: child for child in resolve_children(parent)}


def relative_result(result: Any, parent: Path | None) -> Any:
    """A JSON-object tool result with repo-relative paths; anything else
    (plain text, a JSON list, an answer without a path under a root) is
    returned unchanged."""
    if parent is None or not isinstance(result, str) or not result.startswith("{"):
        return result
    try:
        payload = json.loads(result)
    except ValueError:
        return result
    if not isinstance(payload, dict):
        return result
    out = relative_answer(payload, parent, lambda: child_roots(parent))
    if out is None:
        return result
    from codegraph.server._budget import dumps_answer

    return dumps_answer(out)
