# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Extract HTTP endpoint definitions from source files.
#              Supports FastAPI / Flask / Starlette decorators (on one line or
#              several, path given by position or as path=) and Django
#              urlpatterns (Python), Nuxt server/api file-based routes plus
#              Express/Fastify and NestJS decorators (JS/TS), Spring
#              @*Mapping decorators (Java), and Gin/Echo router calls (Go).
#              Also reads the router prefixes and include calls of a Python
#              file, from which a route's full path is composed.

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EndpointDef:
    id: str  # see endpoint_id
    method: str  # GET, POST, PUT, PATCH, DELETE
    path: str  # URL path, "/donations/{id}"
    framework: str  # "fastapi", "nuxt", "express", "flask", ...
    file_path: str
    start_line: int
    handler_name: str | None = None
    # The object a Python route decorator is called on ("router",
    # "images_router", "app"), which names the router whose prefixes the
    # full path is composed from. Empty for the other frameworks.
    router: str = ""


def endpoint_id(path: str | Path, line: int, method: str, url: str) -> str:
    """The Endpoint node key: file, declaring line, method and local path.

    The line keeps apart two routers of one file that declare the same method
    and path (two ``@x.get("")``); it changes only when the file does, and a
    changed file has its endpoints purged and written again anyway.
    """
    return f"{path}::{line}::{method}::{url}"


# ---------------------------------------------------------------------------
# Python, FastAPI / Flask / Starlette decorators
# ---------------------------------------------------------------------------

# Finds the start of @router.get(...) / @app.post(...) / @bp.route(...) /
# @router.api_route(...). The arguments are read with the ast module from the
# whole decorator, which may span several lines.
_PY_DECORATOR = re.compile(
    r"""^\s*@\s*(?P<obj>[a-zA-Z_][\w.]*)    # router object or attribute access
         \.(?P<method>get|post|put|patch|delete|head|options|route|api_route)
         \s*\(
    """,
    re.VERBOSE,
)

# A decorator call longer than this many lines is not read.
_PY_DECORATOR_MAX_LINES = 40


def _py_decorator_call(lines: list[str], start: int) -> tuple[ast.Call, int] | None:
    """Parse the decorator call opening at ``lines[start]`` (0-based).

    Returns the call and the 0-based index of its last line, or None when no
    complete call parses within the line budget.
    """
    first = lines[start].lstrip()[1:]  # drop the "@"
    text = first
    for end in range(start, min(start + _PY_DECORATOR_MAX_LINES, len(lines))):
        if end > start:
            text += "\n" + lines[end]
        try:
            node = ast.parse(text.strip(), mode="eval").body
        except SyntaxError:
            continue
        return (node, end) if isinstance(node, ast.Call) else None
    return None


def _py_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_python(path: str | Path, src: str) -> list[EndpointDef]:
    out: list[EndpointDef] = []
    lines = src.splitlines()
    for i, line in enumerate(lines):
        m = _PY_DECORATOR.match(line)
        if not m:
            continue
        parsed = _py_decorator_call(lines, i)
        if parsed is None:
            continue
        call, end = parsed
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        url = _py_str(call.args[0]) if call.args else _py_str(kwargs.get("path"))
        if url is None:
            continue
        method = m.group("method").upper()
        router = m.group("obj")

        if method in ("ROUTE", "API_ROUTE"):
            # Methods come from methods=[...]. A methods value that is not a
            # literal list (a module constant) is not known here: ANY.
            methods_node = kwargs.get("methods")
            if methods_node is None:
                methods = ["GET"]
            elif isinstance(methods_node, (ast.List, ast.Tuple, ast.Set)):
                methods = [
                    s.upper()
                    for s in map(_py_str, methods_node.elts)
                    if s and s.strip()
                ]
            else:
                methods = ["ANY"]
            for mth in methods:
                out.append(
                    _build_py_endpoint(path, i + 1, mth, url, lines, end + 1, router)
                )
            continue

        out.append(_build_py_endpoint(path, i + 1, method, url, lines, end + 1, router))
    return out


def _build_py_endpoint(
    path: str | Path,
    line_no: int,
    method: str,
    url: str,
    lines: list[str],
    after: int | None = None,
    router: str = "",
) -> EndpointDef:
    """Build an EndpointDef + sniff the handler function name on the next
    non-decorator line. ``after`` is the 0-based index of the first line past
    the decorator (a decorator may span several lines)."""
    handler = None
    begin = line_no if after is None else after
    for j in range(begin, min(begin + 15, len(lines))):
        ln = lines[j].lstrip()
        if ln.startswith("@"):
            continue  # other decorators stacked
        m = re.match(r"(?:async\s+)?def\s+([a-zA-Z_]\w*)\s*\(", ln)
        if m:
            handler = m.group(1)
            break
    return EndpointDef(
        id=endpoint_id(path, line_no, method, url),
        method=method,
        path=url,
        framework="fastapi",
        file_path=str(path),
        start_line=line_no,
        handler_name=handler,
        router=router,
    )


# ---------------------------------------------------------------------------
# Python router prefixes: APIRouter(prefix=...), Blueprint(url_prefix=...),
# include_router(..., prefix=...), register_blueprint(..., url_prefix=...)
# ---------------------------------------------------------------------------

# Name references the indexer keeps per file, read back at query time to
# compose the full path of a route (see codegraph/analysis/endpoint_query.py).
#   router_def      from_id "<file>::<var>", name = kind (router, blueprint
#                   or app), extra = JSON {"prefix": str | null}, null when
#                   the prefix is not a string literal
#   router_include  from_id "<file>::<parent var>", name "<file>::<var>" of
#                   the router included, extra = JSON {} when no prefix is
#                   given, else {"prefix": str | null}
ROUTER_DEF = "router_def"
ROUTER_INCLUDE = "router_include"

# Constructor name -> (router kind, prefix keyword).
_ROUTER_CTORS = {
    "APIRouter": ("router", "prefix"),
    "Blueprint": ("blueprint", "url_prefix"),
    "FastAPI": ("app", ""),
    "Flask": ("app", ""),
    "Starlette": ("app", ""),
}
# Include call name -> prefix keyword.
_INCLUDE_CALLS = {"include_router": "prefix", "register_blueprint": "url_prefix"}
_ROUTING_HINTS = (*_ROUTER_CTORS, *_INCLUDE_CALLS)


def _dotted(node: ast.expr) -> str | None:
    """``a.b.c`` for a Name / Attribute chain, None for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _prefix_arg(call: ast.Call, keyword: str) -> dict:
    """{} when ``keyword`` is not passed, else {"prefix": literal or None}."""
    for kw in call.keywords:
        if kw.arg == keyword:
            return {"prefix": _py_str(kw.value)}
    return {}


def _import_aliases(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name -> (module, imported name or "") for the file's imports, at
    any depth (an app factory importing its routers in its body is common)."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = (module, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = (a.name, "")
                else:
                    head = a.name.split(".")[0]
                    out[head] = (head, "")
    return out


def _join_module(module: str, *parts: str) -> str:
    rest = ".".join(p for p in parts if p)
    if not rest:
        return module
    return module + rest if module.endswith(".") else f"{module}.{rest}"


def python_router_refs(
    path: str | Path, src: str, resolve_module
) -> list[tuple[str, str, str, str]]:
    """The router definitions and include calls of a Python file, as name
    references (see ROUTER_DEF and ROUTER_INCLUDE).

    ``resolve_module(dotted)`` returns the repo file a module resolves to, or
    None. An included router is named by the file and variable it resolves
    to: a local variable, ``from m import router as x``, or ``mod.router``
    with ``mod`` an imported module. Anything else is left out.
    """
    if not any(h in src for h in _ROUTING_HINTS):
        return []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    file_key = str(path)
    aliases = _import_aliases(tree)
    refs: list[tuple[str, str, str, str]] = []

    def resolve(dotted: str) -> str | None:
        parts = dotted.split(".")
        head, attr = parts[0], parts[-1]
        if head not in aliases:
            return f"{file_key}::{dotted}" if len(parts) == 1 else None
        module, name = aliases[head]
        if len(parts) == 1:
            if not name:
                return None
            target = resolve_module(module)
            return f"{target}::{name}" if target else None
        target = resolve_module(_join_module(module, name, *parts[1:-1]))
        return f"{target}::{attr}" if target else None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
            node.value, ast.Call
        ):
            ctor = _dotted(node.value.func) or ""
            spec = _ROUTER_CTORS.get(ctor.split(".")[-1])
            if spec is None:
                continue
            kind, keyword = spec
            prefix = (
                _prefix_arg(node.value, keyword).get("prefix", "") if keyword else ""
            )
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            refs.extend(
                (
                    ROUTER_DEF,
                    f"{file_key}::{t.id}",
                    kind,
                    json.dumps({"prefix": prefix}),
                )
                for t in targets
                if isinstance(t, ast.Name)
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            keyword = _INCLUDE_CALLS.get(node.func.attr)
            if keyword is None or not node.args:
                continue
            parent = _dotted(node.func.value)
            child = _dotted(node.args[0])
            child_key = resolve(child) if child else None
            if parent is None or child_key is None:
                continue
            refs.append(
                (
                    ROUTER_INCLUDE,
                    f"{file_key}::{parent}",
                    child_key,
                    json.dumps(_prefix_arg(node, keyword)),
                )
            )
    return refs


# ---------------------------------------------------------------------------
# Django, path() / re_path() entries in urls.py urlpatterns
# ---------------------------------------------------------------------------

# path("donations/<int:pk>/", views.detail, name="detail")
# re_path(r"^donations/$", DonationList.as_view())
_DJANGO_ROUTE = re.compile(
    r"""\b(?P<fn>path|re_path)\s*\(
         \s*(?:r)?['"](?P<path>[^'"]*)['"]      # the route pattern
         \s*,\s*(?P<view>[^,)\n]+)              # the view reference
    """,
    re.VERBOSE,
)


def extract_django(path: str | Path, src: str) -> list[EndpointDef]:
    """Django URL routing, path() / re_path() in a urls.py urlpatterns list.

    Django does not pin a method at the route, so we emit method ANY. The
    handler is the view callable (function or `View.as_view()` class name).
    """
    p = Path(path)
    if p.name != "urls.py":
        return []

    out: list[EndpointDef] = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = _DJANGO_ROUTE.search(line)
        if not m:
            continue
        url = m.group("path")
        # Normalise the leading slash so paths read the same as other frameworks
        norm = url if url.startswith("/") else "/" + url
        # Strip a Django regex anchor so /^donations/$ reads as /donations/
        norm = norm.lstrip("/^").rstrip("$")
        norm = "/" + norm

        view = m.group("view").strip()
        as_view = re.search(r"([A-Za-z_]\w*)\s*\.as_view\s*\(", view)
        # views.detail -> detail, app.views.detail -> detail
        handler = as_view.group(1) if as_view else view.split(".")[-1]

        out.append(
            EndpointDef(
                id=endpoint_id(path, i, "ANY", norm),
                method="ANY",
                path=norm,
                framework="django",
                file_path=str(path),
                start_line=i,
                handler_name=handler or None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Nuxt, file-based routes under server/api/
# ---------------------------------------------------------------------------

_NUXT_METHOD_SUFFIX = re.compile(
    r"\.(get|post|put|patch|delete|head|options)\.(ts|js|mjs)$", re.IGNORECASE
)


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
            id=endpoint_id(path, 1, method, route_path),
            method=method,
            path=route_path,
            framework="nuxt",
            file_path=str(path),
            start_line=1,
            handler_name="defineEventHandler",
        )
    ]


# ---------------------------------------------------------------------------
# Express / Fastify, app.get("/x", handler)
# ---------------------------------------------------------------------------

_JS_METHOD_CALL = re.compile(
    r"""(?P<obj>[a-zA-Z_][\w$]*)
         \.(?P<method>get|post|put|patch|delete)
         \s*\(\s*['"`](?P<path>[^'"`]+)['"`]
    """,
    re.VERBOSE,
)


def extract_express(path: str | Path, src: str) -> list[EndpointDef]:
    """Heuristic Express/Fastify extraction, very light, may produce noise."""
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
                id=endpoint_id(path, i, m.group("method").upper(), m.group("path")),
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
# NestJS, @Get('x') / @Post('x') controller method decorators (TS)
# ---------------------------------------------------------------------------

# @Get(), @Get('profile'), @Post("login"), @Delete(':id')
_NEST_DECORATOR = re.compile(
    r"""@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All)
         \s*\(\s*
         (?:['"`](?P<path>[^'"`]*)['"`])?       # optional path argument
         \s*\)
    """,
    re.VERBOSE,
)


def extract_nest(path: str | Path, src: str) -> list[EndpointDef]:
    """NestJS controller route decorators. Path defaults to "/" when the
    decorator is called with no argument (`@Get()`). The handler is the
    method name on the line that follows the decorator."""
    out: list[EndpointDef] = []
    lines = src.splitlines()
    for i, line in enumerate(lines, start=1):
        m = _NEST_DECORATOR.search(line)
        if not m:
            continue
        method = m.group("method")
        if method == "All":
            method = "ANY"
        sub = m.group("path") or ""
        route = "/" + sub.strip("/") if sub else "/"

        handler = None
        for j in range(i, min(i + 5, len(lines))):
            ln = lines[j].lstrip()
            if ln.startswith("@"):
                continue
            hm = re.match(
                r"(?:public\s+|private\s+|protected\s+|async\s+)*([A-Za-z_]\w*)\s*\(",
                ln,
            )
            if hm:
                handler = hm.group(1)
                break

        out.append(
            EndpointDef(
                id=endpoint_id(path, i, method.upper(), route),
                method=method.upper(),
                path=route,
                framework="nestjs",
                file_path=str(path),
                start_line=i,
                handler_name=handler,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Spring, @GetMapping / @RequestMapping(method = RequestMethod.POST) (Java)
# ---------------------------------------------------------------------------

# @GetMapping("/users"), @PostMapping(value = "/users"), @RequestMapping("/x")
_SPRING_MAPPING = re.compile(
    r"""@(?P<ann>Get|Post|Put|Patch|Delete|Request)Mapping
         \s*\(
           (?P<args>[^)]*)
         \)
    """,
    re.VERBOSE,
)
_SPRING_PATH = re.compile(r"""(?:value\s*=\s*|path\s*=\s*)?['"]([^'"]+)['"]""")
_SPRING_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)")


def extract_spring(path: str | Path, src: str) -> list[EndpointDef]:
    """Spring MVC mapping annotations. @RequestMapping infers the method from
    a `method = RequestMethod.X` argument, defaulting to ANY when absent."""
    out: list[EndpointDef] = []
    lines = src.splitlines()
    for i, line in enumerate(lines, start=1):
        m = _SPRING_MAPPING.search(line)
        if not m:
            continue
        ann = m.group("ann")
        args = m.group("args")

        pm = _SPRING_PATH.search(args)
        route = pm.group(1) if pm else "/"

        if ann == "Request":
            mm = _SPRING_METHOD.search(args)
            method = mm.group(1) if mm else "ANY"
        else:
            method = ann.upper()

        handler = None
        for j in range(i, min(i + 5, len(lines))):
            ln = lines[j].lstrip()
            if ln.startswith("@"):
                continue
            hm = re.search(r"\b([A-Za-z_]\w*)\s*\(", ln)
            if hm:
                handler = hm.group(1)
                break

        out.append(
            EndpointDef(
                id=endpoint_id(path, i, method, route),
                method=method,
                path=route,
                framework="spring",
                file_path=str(path),
                start_line=i,
                handler_name=handler,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Gin / Echo, r.GET("/path", handler) (Go)
# ---------------------------------------------------------------------------

# r.GET("/users", listUsers) / e.POST("/users", h.Create) / group.DELETE(...)
_GO_ROUTE = re.compile(
    r"""\b(?P<obj>[A-Za-z_]\w*)
         \.(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any)
         \s*\(\s*['"](?P<path>[^'"]+)['"]
         \s*,\s*(?P<handler>[A-Za-z_][\w.]*)
    """,
    re.VERBOSE,
)


def extract_go(path: str | Path, src: str) -> list[EndpointDef]:
    """Gin and Echo router calls. Both expose `<router>.METHOD(path, handler)`,
    so a single pattern covers them. The handler is the last dotted segment."""
    out: list[EndpointDef] = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = _GO_ROUTE.search(line)
        if not m:
            continue
        method = m.group("method")
        method = "ANY" if method == "Any" else method.upper()
        handler = m.group("handler").split(".")[-1]
        out.append(
            EndpointDef(
                id=endpoint_id(path, i, method, m.group("path")),
                method=method,
                path=m.group("path"),
                framework="gin",
                file_path=str(path),
                start_line=i,
                handler_name=handler or None,
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
        eps = extract_python(p, src)
        eps.extend(extract_django(p, src))
        return eps
    if suffix in (".ts", ".tsx", ".js", ".mjs"):
        # Nuxt first (path-based), then NestJS decorators, then a best-effort
        # express scan. NestJS and Express rarely co-occur in one file.
        nuxt = extract_nuxt(p, src)
        if nuxt:
            return nuxt
        nest = extract_nest(p, src)
        if nest:
            return nest
        return extract_express(p, src)
    if suffix == ".java":
        return extract_spring(p, src)
    if suffix == ".go":
        return extract_go(p, src)
    return []
