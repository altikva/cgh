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
#              suffix fallback, concrete segments filling parameters and
#              catch-alls), method (ANY routes answer every method) and test
#              files.

from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
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


def _rows(conn: Any) -> list[dict]:
    try:
        return conn.find_nodes(
            "Endpoint",
            return_fields=[*_BASE_FIELDS, "router", "is_test"],
            order_by=["path", "method", "file_path", "start_line"],
        )
    except Exception:
        # A child store written before the router / is_test columns.
        try:
            return conn.find_nodes(
                "Endpoint", return_fields=_BASE_FIELDS, order_by=["path", "method"]
            )
        except Exception:
            return []


def _full_paths(ep: dict, routing: _Routing) -> tuple[list[str], bool]:
    """The full paths of one stored route, and whether a chain stopped on a
    prefix that is not a string literal."""
    path = ep.get("path") or ""
    framework = ep.get("framework") or ""
    if framework == "fastapi":
        prefixes, partial = routing.mounts(
            f"{ep['file_path']}::{ep.get('router') or ''}"
        )
        return list(dict.fromkeys(_join(p, path) or "/" for p in prefixes)), partial
    if framework == "nuxt":
        return [path], False
    return [], False


def list_endpoints(conn: Any) -> list[dict]:
    """Every Endpoint of ``conn`` as a dict: the stored fields, ``handler``,
    ``test``, ``full_paths`` and ``full_path_partial``.

    A Python decorator route gets one full path per chain of include calls
    reaching its router; a chain that meets a prefix which is not a string
    literal gives none and sets ``full_path_partial``. A Nuxt route's path is
    already its full path; the other frameworks get none.
    """
    rows = _rows(conn)
    routing = _Routing(conn)
    out: list[dict] = []
    for ep in rows:
        handlers = conn.find_neighbors(
            "IMPLEMENTED_BY", src_key=ep["id"], return_dst=["name"]
        )
        full_paths, partial = _full_paths(ep, routing)
        out.append(
            {
                **ep,
                "handler": handlers[0]["dst_name"] if handlers else None,
                "test": bool(ep.get("is_test")),
                "full_paths": full_paths,
                "full_path_partial": partial,
            }
        )
    return out


class FullPathIndex:
    """The full paths of every route of one graph, keyed by where the route
    is declared, composed on first use and then reused: impact attaches them
    to many endpoint rows of one run."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._by_key: dict[tuple, list[str]] | None = None

    def get(self, file_path: str, method: str, path: str, line: Any) -> list[str]:
        if self._by_key is None:
            routing = _Routing(self._conn)
            self._by_key = {}
            for ep in _rows(self._conn):
                key = (
                    ep.get("file_path") or "",
                    ep.get("method") or "",
                    ep.get("path") or "",
                    ep.get("start_line"),
                )
                found = self._by_key.setdefault(key, [])
                found.extend(p for p in _full_paths(ep, routing)[0] if p not in found)
        return list(self._by_key.get((file_path, method, path, line), []))


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

# _PARAM within one segment, where an Express parameter has no "/" before it.
_SEG_PARAM = re.compile(r"\{[^}/]*\}|<[^>/]*>|^:[A-Za-z_]\w*")

# A segment that takes the rest of the path: Starlette's {name:path}, Flask's
# <path:name>.
_REST = re.compile(r"^(\{[^}/:]*:path\}|<path:[^>/]*>)$")

# One literal segment shared with the query weighs 2, a parameter facing a
# parameter or a segment mixing text and a parameter ("{id}.pdf") taking a
# concrete one 1, a bare parameter or catch-all taking a concrete one nothing.
_LIT, _PARAM_PAIR = 2, 1

# Bounds on a query pattern, far above any real route.
_MAX_PATTERN_CHARS = 2048
_MAX_SEGMENTS = 64


def _norm(path: str) -> str:
    """Parameters as ``{}``, no trailing slash: the form paths compare in."""
    p = _PARAM.sub("{}", path)
    return p.rstrip("/") if len(p) > 1 else p


def _split(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


@lru_cache(maxsize=4096)
def _stored_segment(seg: str) -> tuple[str, Any]:
    """A stored path segment as ``("rest", None)`` for a catch-all,
    ``("lit", text)`` with no parameter, else ``("param", (norm, regex))``
    where the regex accepts the concrete segments it stands for."""
    if _REST.match(seg):
        return ("rest", None)
    if not _SEG_PARAM.search(seg):
        return ("lit", seg)
    # Adjacent parameters ("{a}{b}") collapse into one "[^/]+": back to back
    # they would backtrack polynomially on a long query segment.
    pattern = ""
    for k, part in enumerate(_SEG_PARAM.split(seg)):
        if k and not pattern.endswith("[^/]+"):
            pattern += "[^/]+"
        pattern += re.escape(part)
    regex = re.compile(pattern + "$")
    return ("param", (_SEG_PARAM.sub("{}", seg), regex))


def _segment_score(stored: tuple[str, Any], query: str) -> int | None:
    """How one stored segment answers one query segment: a score, or None
    when it does not match."""
    kind, value = stored
    if kind == "lit":
        return _LIT if value == query else None
    norm, regex = value
    if _SEG_PARAM.search(query):
        # A parameter in the query only meets a parameter of the same shape.
        return _PARAM_PAIR if _SEG_PARAM.sub("{}", query) == norm else None
    if not regex.match(query):
        return None
    # "{id}.pdf" says more about "123.pdf" than a bare "{id}" does.
    return _PARAM_PAIR if norm != "{}" else 0


def _align(
    stored: list[tuple[str, Any]], query: list[str]
) -> tuple[int, int, bool] | None:
    """The best way ``stored`` matches all of ``query``:
    ``(score, shared literal segments, catch-all used)``, or None.

    Memoised on (stored index, query index), so several catch-alls cost
    O(len(stored) * len(query)**2) instead of growing exponentially."""
    memo: dict[tuple[int, int], tuple[int, int, bool] | None] = {}

    def better(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return a if (a[0], a[1], not a[2]) >= (b[0], b[1], not b[2]) else b

    def walk(i: int, j: int) -> tuple[int, int, bool] | None:
        if i == len(stored):
            return (0, 0, False) if j == len(query) else None
        if j == len(query):
            return None
        key = (i, j)
        if key in memo:
            return memo[key]
        seg = stored[i]
        best: tuple[int, int, bool] | None = None
        if seg[0] == "rest":
            # One or more segments, whatever they are.
            for k in range(j + 1, len(query) + 1):
                tail = walk(i + 1, k)
                if tail is not None:
                    best = better(best, (tail[0], tail[1], True))
        else:
            got = _segment_score(seg, query[j])
            if got is not None:
                tail = walk(i + 1, j + 1)
                if tail is not None:
                    best = (tail[0] + got, tail[1] + (got == _LIT), tail[2])
        memo[key] = best
        return best

    return walk(0, 0)


def _path_match(stored: str, query: str, suffix: bool) -> tuple[int, bool] | None:
    """``(score, catch-all used)`` when ``query`` is ``stored`` (concrete
    segments filling its parameters), or with ``suffix`` a segment-aligned
    suffix of it; else None. A match must share a literal segment, so a
    query of ``/``, a lone parameter or a concrete value alone says nothing;
    only an exact match on the normalised form (``/`` for ``/``) needs none.
    """
    segs = [_stored_segment(s) for s in _split(stored)]
    q = _split(query)
    if not suffix:
        got = _align(segs, q)
        if got and got[1]:
            return got[0], got[2]
        if _norm(stored) == _norm(query):
            return (got[0] if got else 0), False
        return None
    # A catch-all route answers only an exact full path: as a suffix it
    # would take any query sharing one literal with its prefix.
    if any(kind == "rest" for kind, _v in segs):
        return None
    best: tuple[int, int, bool] | None = None
    for i in range(1, len(segs)):
        got = _align(segs[i:], q)
        if got and got[1] and (best is None or got[0] > best[0]):
            best = got
    return (best[0], best[2]) if best else None


def _is_glob(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def _glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(
        _norm(path), _norm(pattern)
    )


def _paths_of(ep: dict) -> tuple[list[str], str]:
    """The paths a route is matched on: its full paths, or its local path
    when it has none, with the match type that answer gets."""
    if ep.get("full_paths"):
        return ep["full_paths"], "full_path"
    return [ep.get("path") or ""], "path"


def _best(paths: list[str], pattern: str, suffix: bool) -> tuple[int, bool] | None:
    found = [m for m in (_path_match(p, pattern, suffix) for p in paths) if m]
    return max(found, key=lambda m: (not m[1], m[0])) if found else None


# Sort key of a match: kind of match, then a catch-all after any other route,
# then the higher score first.
_Key = tuple[int, int, int]


def _match(
    candidates: list[tuple[str, dict]], pattern: str
) -> list[tuple[str, dict, str | None, _Key]]:
    """(scope, endpoint, how, sort key) for the candidates ``pattern``
    selects."""
    if not pattern:
        return [(scope, ep, None, (0, 0, 0)) for scope, ep in candidates]
    if _is_glob(pattern):
        out = []
        for scope, ep in candidates:
            paths, how = _paths_of(ep)
            if any(_glob(p, pattern) for p in paths):
                out.append((scope, ep, how, (_RANK[how], 0, 0)))
        return out
    exact = []
    for scope, ep in candidates:
        paths, how = _paths_of(ep)
        m = _best(paths, pattern, suffix=False)
        if m:
            exact.append((scope, ep, how, (_RANK[how], int(m[1]), -m[0])))
    if exact or not pattern.startswith("/"):
        return exact
    out = []
    for scope, ep in candidates:
        paths, _how = _paths_of(ep)
        m = _best(paths, pattern, suffix=True)
        if m:
            out.append((scope, ep, "suffix", (_RANK["suffix"], int(m[1]), -m[0])))
    return out


def _method_ok(ep: dict, wanted: str | None) -> bool:
    """A route declared for any method (ANY) answers every method filter."""
    if not wanted:
        return True
    have = (ep.get("method") or "").upper()
    return have in (wanted, "ANY")


def select_endpoints(
    per_scope: list[tuple[str, list[dict]]],
    path_pattern: str = "",
    method: str = "",
    include_tests: bool = False,
    short_path: Callable[[str], str] = lambda p: p,
) -> dict:
    """The endpoints tool payload: ``{total, by_framework, tests_excluded}``.

    ``path_pattern`` matches a route's full paths (its local path when it has
    none), as a glob or exactly: parameter names are ignored, a stored
    parameter takes any concrete segment and a catch-all ({p:path}) the rest
    of the path. When a pattern starting with ``/`` matches nothing that way,
    it matches the routes it is a segment-aligned suffix of, flagged
    ``match: "suffix"``. Exact matches come first, a catch-all after a more
    specific route, then the most literal segments shared. ``method`` keeps
    that method's routes and the ANY ones. Routes declared in test files are
    left out unless ``include_tests``; ``tests_excluded`` counts them.
    """
    method_filter = method.strip().upper() or None
    pattern = path_pattern.strip()
    if len(pattern) > _MAX_PATTERN_CHARS or len(_split(pattern)) > _MAX_SEGMENTS:
        # No real route is this long; the cap bounds the matching cost of a
        # pattern an agent or a script passes in.
        return {
            "total": 0,
            "by_framework": {},
            "tests_excluded": 0,
            "error": (
                f"path_pattern too long (max {_MAX_PATTERN_CHARS} characters, "
                f"{_MAX_SEGMENTS} segments)"
            ),
        }
    real: list[tuple[str, dict]] = []
    tests: list[tuple[str, dict]] = []
    for scope, rows in per_scope:
        for ep in rows:
            if not _method_ok(ep, method_filter):
                continue
            (tests if ep.get("test") else real).append((scope, ep))
    if include_tests:
        real, tests = real + tests, []
    matched = _match(real, pattern)
    # The test routes --include-tests would add to this very answer.
    tests_excluded = (
        sum(1 for m in _match(real + tests, pattern) if m[1].get("test"))
        if tests
        else 0
    )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for scope, ep, how, _key in sorted(
        matched,
        key=lambda m: (
            m[3],
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
