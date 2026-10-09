# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The endpoints query shared by the MCP endpoints tool and the
#              cgh endpoints command. Reads the stored routes with their
#              handler, composes each Python route's full paths from the
#              router prefixes and include calls the indexer kept, and
#              filters by path pattern (exact, glob, then a segment-aligned
#              suffix fallback), method and test files.

from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from codegraph.analysis.endpoints import ROUTER_DEF, ROUTER_INCLUDE

# Router includes followed upward at most this deep.
_MAX_DEPTH = 8

_BASE_FIELDS = ["id", "method", "path", "framework", "file_path", "start_line"]

# A path parameter in FastAPI / Starlette ({id}), Flask (<int:id>) or
# Express / Nuxt (:id) form.
_PARAM = re.compile(r"\{[^}/]*\}|<[^>/]*>|(?<=/):[A-Za-z_]\w*")

_RANK = {"full_path": 0, "path": 1, "suffix": 2}


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    return prefix.rstrip("/") + "/" + path.lstrip("/")


class _Routing:
    """The router prefixes and include calls of one graph."""

    def __init__(self, conn: Any) -> None:
        self.defs: dict[str, tuple[str, str | None]] = {}
        self.parents: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self._cache: dict[str, tuple[list[str], bool]] = {}
        reader = getattr(conn, "name_refs_of_kind", None)
        if reader is None:
            return
        for from_id, kind, extra in reader(ROUTER_DEF):
            self.defs[from_id] = (kind, _loads(extra).get("prefix", ""))
        for from_id, child, extra in reader(ROUTER_INCLUDE):
            self.parents[child].append((from_id, _loads(extra)))

    def mounts(self, key: str) -> tuple[list[str], bool]:
        """The prefixes router ``key`` serves its routes under (its own
        prefix included), and whether a chain stopped on a prefix that is not
        a string literal."""
        if key not in self._cache:
            self._cache[key] = self._mounts(key, frozenset(), 0)
        return self._cache[key]

    def _mounts(
        self, key: str, seen: frozenset[str], depth: int
    ) -> tuple[list[str], bool]:
        kind, own = self.defs.get(key, ("", ""))
        parents = [] if kind == "app" else self.parents.get(key, [])
        # A parent already on the chain is a cycle: no mount through it.
        parents = [(p, inc) for p, inc in parents if p not in seen]
        if not parents or depth >= _MAX_DEPTH:
            return ([own], False) if own is not None else ([], True)
        out: list[str] = []
        partial = False
        for parent, include in parents:
            if "prefix" in include and include["prefix"] is None:
                partial = True
                continue
            given = include.get("prefix", "")
            if kind == "blueprint" and "prefix" in include:
                mine = given  # register_blueprint(url_prefix=) replaces it
            elif own is None:
                partial = True
                continue
            else:
                mine = _join(given, own)
            ups, up_partial = self._mounts(parent, seen | {key}, depth + 1)
            partial = partial or up_partial
            out.extend(_join(up, mine) for up in ups)
        return list(dict.fromkeys(out)), partial


def _loads(extra: str) -> dict:
    try:
        value = json.loads(extra or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def list_endpoints(conn: Any) -> list[dict]:
    """Every Endpoint of ``conn`` as a dict: the stored fields, ``handler``,
    ``test``, ``full_paths`` and ``full_path_partial``.

    A Python decorator route gets one full path per chain of include calls
    reaching its router; a chain that meets a prefix which is not a string
    literal gives none and sets ``full_path_partial``. A Nuxt route's path is
    already its full path; the other frameworks get none.
    """
    try:
        rows = conn.find_nodes(
            "Endpoint",
            return_fields=[*_BASE_FIELDS, "router", "is_test"],
            order_by=["path", "method", "file_path", "start_line"],
        )
    except Exception:
        # A child store written before the router / is_test columns.
        try:
            rows = conn.find_nodes(
                "Endpoint", return_fields=_BASE_FIELDS, order_by=["path", "method"]
            )
        except Exception:
            return []
    routing = _Routing(conn)
    out: list[dict] = []
    for ep in rows:
        handlers = conn.find_neighbors(
            "IMPLEMENTED_BY", src_key=ep["id"], return_dst=["name"]
        )
        path = ep.get("path") or ""
        framework = ep.get("framework") or ""
        full_paths: list[str] = []
        partial = False
        if framework == "fastapi":
            prefixes, partial = routing.mounts(
                f"{ep['file_path']}::{ep.get('router') or ''}"
            )
            full_paths = [_join(p, path) or "/" for p in prefixes]
        elif framework == "nuxt":
            full_paths = [path]
        out.append(
            {
                **ep,
                "handler": handlers[0]["dst_name"] if handlers else None,
                "test": bool(ep.get("is_test")),
                "full_paths": list(dict.fromkeys(full_paths)),
                "full_path_partial": partial,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    """Parameters as ``{}``, no trailing slash: the form paths compare in."""
    p = _PARAM.sub("{}", path)
    return p.rstrip("/") if len(p) > 1 else p


def _segments(path: str) -> list[str]:
    return [s for s in _norm(path).split("/") if s]


def _is_glob(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def _same(path: str, pattern: str, glob: bool) -> bool:
    if glob:
        return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(
            _norm(path), _norm(pattern)
        )
    return path == pattern or _norm(path) == _norm(pattern)


def _direct_match(ep: dict, pattern: str, glob: bool) -> str | None:
    if any(_same(fp, pattern, glob) for fp in ep["full_paths"]):
        return "full_path"
    if _same(ep.get("path") or "", pattern, glob):
        return "path"
    return None


def _suffix_match(local: str, pattern: str) -> int:
    """A score above 0 when ``local`` is a segment-aligned suffix of
    ``pattern`` (a parameter on either side matching any segment), else 0.
    At least one literal segment must be shared, and a local path too generic
    to say anything ("", "/", a lone parameter) never matches. The more
    segments line up exactly, the higher the score."""
    mine = _segments(local)
    theirs = _segments(pattern)
    if not mine or mine == ["{}"] or len(mine) > len(theirs):
        return 0
    tail = theirs[len(theirs) - len(mine) :]
    pairs = list(zip(mine, tail, strict=True))
    if not all(a == b or "{}" in (a, b) for a, b in pairs):
        return 0
    # "/health" is no suffix of "/x/{id}" just because a parameter matches.
    if not any(a == b != "{}" for a, b in pairs):
        return 0
    # A literal segment in common weighs 2, a parameter facing a parameter 1,
    # a parameter facing a literal nothing.
    return sum(2 if a != "{}" else 1 for a, b in pairs if a == b)


def _match(
    candidates: list[tuple[str, dict]], pattern: str
) -> list[tuple[str, dict, str | None]]:
    """(scope, endpoint, how) for the candidates ``pattern`` selects."""
    if not pattern:
        return [(scope, ep, None) for scope, ep in candidates]
    glob = _is_glob(pattern)
    matched = []
    for scope, ep in candidates:
        how = _direct_match(ep, pattern, glob)
        if how:
            matched.append((scope, ep, how))
    if matched or glob or not pattern.startswith("/"):
        return matched
    return [
        (scope, ep, "suffix")
        for scope, ep in candidates
        if _suffix_match(ep.get("path") or "", pattern)
    ]


def select_endpoints(
    per_scope: list[tuple[str, list[dict]]],
    path_pattern: str = "",
    method: str = "",
    include_tests: bool = False,
    short_path: Callable[[str], str] = lambda p: p,
) -> dict:
    """The endpoints tool payload: ``{total, by_framework, tests_excluded}``.

    ``path_pattern`` matches a route's full paths, then its local path, as a
    glob or exactly (parameter names ignored). When a pattern starting with
    ``/`` matches nothing that way, routes whose local path is a suffix of it
    match instead, flagged ``match: "suffix"`` since several routers can hold
    the same local path. Routes declared in test files are left out unless
    ``include_tests``; ``tests_excluded`` counts them.
    """
    method_filter = method.strip().upper() or None
    pattern = path_pattern.strip()
    real: list[tuple[str, dict]] = []
    tests: list[tuple[str, dict]] = []
    for scope, rows in per_scope:
        for ep in rows:
            if method_filter and (ep.get("method") or "") != method_filter:
                continue
            (tests if ep.get("test") else real).append((scope, ep))
    if include_tests:
        real, tests = real + tests, []
    matched = _match(real, pattern)
    # The test routes --include-tests would add to this very answer.
    tests_excluded = (
        sum(1 for _s, ep, _h in _match(real + tests, pattern) if ep.get("test"))
        if tests
        else 0
    )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for scope, ep, how in sorted(
        matched,
        key=lambda m: (
            _RANK.get(m[2] or "", 0),
            -_suffix_match(m[1].get("path") or "", pattern) if m[2] == "suffix" else 0,
            (m[1].get("full_paths") or [m[1].get("path") or ""])[0],
            m[1].get("path") or "",
            m[1].get("method") or "",
            m[1].get("file_path") or "",
            m[1].get("start_line") or 0,
        ),
    ):
        row: dict[str, Any] = {
            "scope": scope,
            "method": ep.get("method") or "",
            "path": ep.get("path") or "",
            "full_paths": ep.get("full_paths") or [],
            "handler": ep.get("handler"),
            "file": short_path(ep["file_path"]),
            "line": ep.get("start_line"),
        }
        if ep.get("full_path_partial"):
            row["full_path_partial"] = True
        if ep.get("test"):
            row["test"] = True
        if how:
            row["match"] = how
        grouped[ep.get("framework") or "unknown"].append(row)

    return {
        "total": sum(len(v) for v in grouped.values()),
        "by_framework": dict(grouped),
        "tests_excluded": tests_excluded,
    }
