# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Extract HTTP endpoint definitions from source files.
#              Supports FastAPI / Flask / Starlette decorators (Python),
#              Nuxt server/api file-based routes, and Express/Fastify .route()
#              calls (JS/TS).

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EndpointDef:
    id: str  # "<file>::<method>::<path>"
    method: str  # GET, POST, PUT, PATCH, DELETE
    path: str  # URL path — "/donations/{id}"
    framework: str  # "fastapi", "nuxt", "express", "flask", ...
    file_path: str
    start_line: int
    handler_name: str | None = None


# ---------------------------------------------------------------------------
# Python — FastAPI / Flask / Starlette decorators
# ---------------------------------------------------------------------------

# Captures @router.get("/path") / @app.post("/path", ...) / @bp.route(...)
_PY_DECORATOR = re.compile(
    r"""@\s*(?P<obj>[a-zA-Z_][\w.]*)       # router object or attribute access
         \.(?P<method>get|post|put|patch|delete|head|options|route)
         \s*\(
           \s*['"](?P<path>[^'"]+)['"]     # the path string
    """,
    re.VERBOSE,
)

# If @bp.route("/x", methods=["POST"]) is used, extract the methods list
_PY_ROUTE_METHODS = re.compile(r"methods\s*=\s*\[([^\]]+)\]")


def extract_python(path: str | Path, src: str) -> list[EndpointDef]:
    out: list[EndpointDef] = []
    lines = src.splitlines()
    for i, line in enumerate(lines, start=1):
        m = _PY_DECORATOR.search(line)
        if not m:
            continue
        method = m.group("method").upper()
        url = m.group("path")

        if method == "ROUTE":
            # Flask-style: infer methods from methods=[...]
            mm = _PY_ROUTE_METHODS.search(line)
            if mm:
                for raw in mm.group(1).split(","):
                    mth = raw.strip().strip("\"'").upper()
                    if not mth:
                        continue
                    out.append(_build_py_endpoint(path, i, mth, url, lines))
            else:
                out.append(_build_py_endpoint(path, i, "GET", url, lines))
            continue

        out.append(_build_py_endpoint(path, i, method, url, lines))
    return out


def _build_py_endpoint(
    path: str | Path,
    line_no: int,
    method: str,
    url: str,
    lines: list[str],
) -> EndpointDef:
    """Build an EndpointDef + sniff the handler function name on the next non-decorator line."""
    handler = None
    for j in range(line_no, min(line_no + 15, len(lines))):
        ln = lines[j].lstrip()
        if ln.startswith("@"):
            continue  # other decorators stacked
        m = re.match(r"(?:async\s+)?def\s+([a-zA-Z_]\w*)\s*\(", ln)
        if m:
            handler = m.group(1)
            break
    return EndpointDef(
        id=f"{path}::{method}::{url}",
        method=method,
        path=url,
        framework="fastapi",
        file_path=str(path),
        start_line=line_no,
        handler_name=handler,
    )


# ---------------------------------------------------------------------------
# Nuxt — file-based routes under server/api/
# ---------------------------------------------------------------------------

_NUXT_METHOD_SUFFIX = re.compile(r"\.(get|post|put|patch|delete|head|options)\.(ts|js|mjs)$", re.IGNORECASE)


def extract_nuxt(path: str | Path, src: str) -> list[EndpointDef]:
    """
    Nuxt server/api routes use file-based routing:
      server/api/donations.get.ts          → GET /api/donations
      server/api/donations/[id].patch.ts   → PATCH /api/donations/:id
      server/api/hello.ts                  → any method (defaults to GET)

    We only emit an Endpoint when the file lives under a `server/api/`
    path. Method defaults to GET if no `.verb.` suffix exists.
    """
    p = Path(path)
    rel = str(p).replace("\\", "/")
    if "/server/api/" not in rel:
        return []

    # Path derivation
    idx = rel.rfind("/server/api/")
    route_path = rel[idx + len("/server") :]  # keep /api/...
    # strip extension + optional .verb suffix
    m = _NUXT_METHOD_SUFFIX.search(route_path)
    if m:
        method = m.group(1).upper()
        route_path = route_path[: m.start()]
    else:
        method = "GET"
        route_path = re.sub(r"\.(ts|js|mjs)$", "", route_path)
    # [id] → :id
    route_path = re.sub(r"\[(\.\.\.)?([\w-]+)\]", r":\2", route_path)
    # index → collection root
    route_path = re.sub(r"/index$", "", route_path) or "/"

    return [
        EndpointDef(
            id=f"{path}::{method}::{route_path}",
            method=method,
            path=route_path,
            framework="nuxt",
            file_path=str(path),
            start_line=1,
            handler_name="defineEventHandler",
        )
    ]


# ---------------------------------------------------------------------------
# Express / Fastify — app.get("/x", handler)
# ---------------------------------------------------------------------------

_JS_METHOD_CALL = re.compile(
    r"""(?P<obj>[a-zA-Z_][\w$]*)
         \.(?P<method>get|post|put|patch|delete)
         \s*\(\s*['"`](?P<path>[^'"`]+)['"`]
    """,
    re.VERBOSE,
)


def extract_express(path: str | Path, src: str) -> list[EndpointDef]:
    """Heuristic Express/Fastify extraction — very light, may produce noise."""
    out: list[EndpointDef] = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = _JS_METHOD_CALL.search(line)
        if not m:
            continue
        obj = m.group("obj").lower()
        # Only accept common router-ish names to reduce false positives
        if obj not in {"app", "router", "fastify", "api", "server", "routes"}:
            continue
        out.append(
            EndpointDef(
                id=f"{path}::{m.group('method').upper()}::{m.group('path')}",
                method=m.group("method").upper(),
                path=m.group("path"),
                framework="express",
                file_path=str(path),
                start_line=i,
                handler_name=None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def extract(path: str | Path, src: str) -> list[EndpointDef]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".py":
        return extract_python(p, src)
    if suffix in (".ts", ".tsx", ".js", ".mjs"):
        # Nuxt first (path-based), then a best-effort express scan
        nuxt = extract_nuxt(p, src)
        if nuxt:
            return nuxt
        return extract_express(p, src)
    return []
