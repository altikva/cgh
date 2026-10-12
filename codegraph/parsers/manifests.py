# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Reads package manifests and MCP client config files into plain
#              records the indexer stores as graph nodes:
#                package.json    one npm PackageDef: name, version, its
#                                dependencies per group, the workspace globs
#                                it declares and the workspace root that
#                                lists it as a member (npm, pnpm, yarn)
#                pyproject.toml  one python PackageDef: [project] or
#                                [tool.poetry] dependencies, optional extras,
#                                dependency groups, and uv workspace members
#                .mcp.json, .cursor/mcp.json, .vscode/mcp.json
#                                one McpServerDef per declared server: name,
#                                transport, command or url. Env and header
#                                VALUES are never kept, only their key names,
#                                and arguments or urls that look like they
#                                carry a credential are masked.
#              read_manifest(path) is the single entry point; it returns
#              None for any other file and never raises.

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from codegraph.imports.workspaces import (
    _build_package_map,
    _find_workspace_root,
    _match_glob_dirs,
    _workspace_globs,
)

NPM = "npm"
PYTHON = "python"

# Files an MCP client reads its server list from: the file name, and the
# directory it must sit in (None = any directory).
_MCP_FILES: dict[str, tuple[str | None, ...]] = {
    ".mcp.json": (None,),
    "mcp.json": (".cursor", ".vscode"),
}

# npm dependency maps and the group each one is reported under.
_NPM_GROUPS = (
    ("dependencies", "runtime"),
    ("devDependencies", "dev"),
    ("peerDependencies", "peer"),
    ("optionalDependencies", "optional"),
)

_MASK = "***"
# Flag or key names whose value is a credential, in args and url queries.
_SECRET_NAME = re.compile(
    r"(token|secret|passw(or)?d|api[-_]?key|apikey|auth|credential|bearer|private[-_]?key)",
    re.IGNORECASE,
)
_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


@dataclass(slots=True)
class DependencyRef:
    """One dependency a manifest declares: the name as written, its version
    spec (or path / workspace protocol) and the group it belongs to."""

    name: str
    spec: str = ""
    group: str = "runtime"


@dataclass(slots=True)
class PackageDef:
    """A package a manifest declares. ``key`` is the ecosystem-qualified,
    normalized name dependencies are matched on ("" for an unnamed manifest,
    which nothing can depend on)."""

    id: str
    name: str
    key: str
    ecosystem: str
    file_path: str
    start_line: int = 1
    version: str = ""
    workspaces: list[str] = field(default_factory=list)
    workspace_root: str = ""
    deps: list[DependencyRef] = field(default_factory=list)


@dataclass(slots=True)
class McpServerDef:
    """An MCP server declared in a client config file. Holds no env or
    header value: ``env_keys`` and ``header_keys`` are the key names only."""

    id: str
    name: str
    file_path: str
    start_line: int = 1
    transport: str = ""
    command: str = ""
    url: str = ""
    args: list[str] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    header_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Manifest:
    """What read_manifest found in one file."""

    packages: list[PackageDef] = field(default_factory=list)
    mcp_servers: list[McpServerDef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def package_key(ecosystem: str, name: str) -> str:
    """The key a dependency is matched on: npm names as written, Python names
    normalized the way installers compare them (case, runs of -_. folded)."""
    if not name:
        return ""
    if ecosystem == PYTHON:
        name = re.sub(r"[-_.]+", "-", name).lower()
    return f"{ecosystem}:{name}"


def is_mcp_config(path: str | Path) -> bool:
    p = Path(path)
    dirs = _MCP_FILES.get(p.name)
    if dirs is None:
        return False
    return None in dirs or p.parent.name in dirs


def is_manifest(path: str | Path) -> bool:
    """True for the files read_manifest understands."""
    name = Path(path).name
    return name in ("package.json", "pyproject.toml") or is_mcp_config(path)


def hidden_path_allowed(parts: tuple[str, ...] | list[str]) -> bool:
    """True when a relative path whose parts include a dot-name is still worth
    indexing: an MCP config file in a dot directory (.cursor/mcp.json) or a
    dot file itself (.mcp.json), with no other hidden part on the way."""
    if not parts or not is_mcp_config(Path(*parts)):
        return False
    rest = parts[:-1]
    if parts[-1] == "mcp.json":
        rest = parts[:-2]
    return not any(part.startswith(".") for part in rest)


def mcp_config_files(root: Path) -> list[Path]:
    """The MCP config files at the top of ``root``, for discovery walks that
    prune every dot directory."""
    out = [root / ".mcp.json"]
    out += [root / d / "mcp.json" for d in (".cursor", ".vscode")]
    return [p for p in out if p.is_file()]


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _line_of(lines: list[str], key: str, start: int = 1) -> int:
    """Line of ``key`` written as a JSON / TOML key at or after ``start``."""
    pat = re.compile(rf"""^\s*['"]?{re.escape(key)}['"]?\s*[:=]""")
    for i in range(max(start, 1), len(lines) + 1):
        if pat.match(lines[i - 1]):
            return i
    return start if start > 1 else 1


def _strip_jsonc(text: str) -> str:
    """JSON with comments and trailing commas (VS Code writes its mcp.json
    that way) turned into plain JSON. Strings are copied untouched."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_json(text: str, lenient: bool) -> object:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        if not lenient:
            return None
    try:
        return json.loads(_strip_jsonc(text))
    except (ValueError, RecursionError):
        return None


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------


def _npm_workspace_root(pkg_dir: Path) -> str:
    """The workspace root whose globs list ``pkg_dir`` as a member, found the
    way the import resolver finds it, or ""."""
    root = _find_workspace_root(pkg_dir.parent)
    if root is None:
        return ""
    try:
        members = _build_package_map(root).values()
    except OSError:
        return ""
    target = pkg_dir.resolve()
    return str(root) if any(d.resolve() == target for d in members) else ""


def _npm_package(path: Path, data: dict, lines: list[str]) -> PackageDef:
    name = _str(data.get("name"))
    deps: list[DependencyRef] = []
    for key, group in _NPM_GROUPS:
        table = data.get(key)
        if not isinstance(table, dict):
            continue
        for dep, spec in table.items():
            if isinstance(dep, str) and dep:
                deps.append(DependencyRef(dep, _str(spec), group))
    workspaces = _workspace_globs(path.parent) if "workspaces" in data else []
    if not workspaces and (path.parent / "pnpm-workspace.yaml").is_file():
        workspaces = _workspace_globs(path.parent)
    return PackageDef(
        id=f"{path}::package",
        name=name,
        key=package_key(NPM, name),
        ecosystem=NPM,
        file_path=str(path),
        start_line=_line_of(lines, "name") if name else 1,
        version=_str(data.get("version")),
        workspaces=workspaces,
        workspace_root=_npm_workspace_root(path.parent),
        deps=deps,
    )


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _pep508(requirement: str, group: str) -> DependencyRef | None:
    m = _PEP508_NAME.match(requirement)
    if not m:
        return None
    return DependencyRef(m.group(1), requirement[m.end() :].strip(), group)


def _poetry_spec(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("version", "path", "git", "url"):
            if isinstance(value.get(key), str):
                return value[key] if key == "version" else f"{key}:{value[key]}"
    return ""


def _python_deps(data: dict) -> list[DependencyRef]:
    deps: list[DependencyRef] = []
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    for req in _str_list(project.get("dependencies")):
        dep = _pep508(req, "runtime")
        if dep:
            deps.append(dep)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra, reqs in optional.items():
            for req in _str_list(reqs):
                dep = _pep508(req, f"optional:{extra}")
                if dep:
                    deps.append(dep)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for name, reqs in groups.items():
            # {include-group = "..."} entries name another group, not a package.
            for req in _str_list(reqs):
                dep = _pep508(req, "dev" if name == "dev" else f"group:{name}")
                if dep:
                    deps.append(dep)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    uv = tool.get("uv") if isinstance(tool.get("uv"), dict) else {}
    for req in _str_list(uv.get("dev-dependencies")):
        dep = _pep508(req, "dev")
        if dep:
            deps.append(dep)
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    tables: list[tuple[str, object]] = [
        ("runtime", poetry.get("dependencies")),
        ("dev", poetry.get("dev-dependencies")),
    ]
    if isinstance(poetry.get("group"), dict):
        tables += [
            ("dev" if g == "dev" else f"group:{g}", t.get("dependencies"))
            for g, t in poetry["group"].items()
            if isinstance(t, dict)
        ]
    for group, table in tables:
        if not isinstance(table, dict):
            continue
        for name, value in table.items():
            if isinstance(name, str) and name.lower() != "python":
                deps.append(DependencyRef(name, _poetry_spec(value), group))
    return deps


def _uv_workspace(data: dict) -> dict | None:
    tool = data.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    ws = uv.get("workspace") if isinstance(uv, dict) else None
    return ws if isinstance(ws, dict) else None


def _uv_members(root: Path, ws: dict) -> set[Path]:
    """Member directories of a uv workspace, through the same glob expansion
    the npm workspaces use."""
    excludes = _str_list(ws.get("exclude"))
    out: set[Path] = set()
    for pattern in _str_list(ws.get("members")):
        for d in _match_glob_dirs(root, pattern):
            rel = d.relative_to(root).as_posix()
            if not any(fnmatch.fnmatch(rel, ex) for ex in excludes):
                out.add(d.resolve())
    return out


def _uv_workspace_root(pkg_dir: Path) -> str:
    """The nearest enclosing uv workspace root that lists ``pkg_dir``."""
    target = pkg_dir.resolve()
    for d in target.parents:
        manifest = d / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        ws = _uv_workspace(data)
        if ws is not None:
            return str(d) if target in _uv_members(d, ws) else ""
    return ""


def _python_package(path: Path, data: dict, lines: list[str]) -> PackageDef | None:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    name = _str(project.get("name")) or _str(poetry.get("name"))
    version = _str(project.get("version")) or _str(poetry.get("version"))
    deps = _python_deps(data)
    ws = _uv_workspace(data)
    # A pyproject.toml that only configures tools declares no package.
    if not name and not deps and ws is None:
        return None
    workspaces = []
    if ws is not None:
        workspaces = _str_list(ws.get("members"))
        workspaces += [f"!{p}" for p in _str_list(ws.get("exclude"))]
    return PackageDef(
        id=f"{path}::package",
        name=name,
        key=package_key(PYTHON, name),
        ecosystem=PYTHON,
        file_path=str(path),
        start_line=_line_of(lines, "name") if name else 1,
        version=version,
        workspaces=workspaces,
        workspace_root=_uv_workspace_root(path.parent),
        deps=deps,
    )


# ---------------------------------------------------------------------------
# MCP client configs
# ---------------------------------------------------------------------------


def _safe_url(url: str) -> str:
    """The url without user info, and with credential-like query values
    masked."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _MASK
    netloc = parts.netloc.rsplit("@", 1)[-1]
    query = "&".join(
        f"{k}={_MASK}" if _SECRET_NAME.search(k) else (f"{k}={v}" if sep else k)
        for k, sep, v in (q.partition("=") for q in parts.query.split("&") if q)
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def _safe_args(args: list[str]) -> list[str]:
    """Arguments with the value of any credential-like flag masked
    (--api-key X, --token=X), and urls cleaned like the server url."""
    out: list[str] = []
    mask_next = False
    for arg in args:
        if mask_next and not arg.startswith("-"):
            out.append(_MASK)
            mask_next = False
            continue
        mask_next = False
        flag, eq, _value = arg.partition("=")
        if arg.startswith("-") and _SECRET_NAME.search(flag):
            if eq:
                out.append(f"{flag}={_MASK}")
            else:
                out.append(arg)
                mask_next = True
        elif "://" in arg:
            out.append(_safe_url(arg))
        elif eq and _SECRET_NAME.search(flag):
            out.append(f"{flag}={_MASK}")
        else:
            out.append(arg)
    return out


def _mcp_servers(path: Path, data: dict, lines: list[str]) -> list[McpServerDef]:
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers")
    if not isinstance(servers, dict):
        return []
    out: list[McpServerDef] = []
    container = _line_of(lines, "mcpServers") if "mcpServers" in data else 1
    for name, spec in servers.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        command = _str(spec.get("command"))
        url = _str(spec.get("url")) or _str(spec.get("serverUrl"))
        transport = _str(spec.get("type")) or _str(spec.get("transport"))
        if not transport:
            transport = "stdio" if command else ("http" if url else "")
        env = spec.get("env")
        headers = spec.get("headers")
        out.append(
            McpServerDef(
                id=f"{path}::mcp::{name}",
                name=name,
                file_path=str(path),
                start_line=_line_of(lines, name, container),
                transport=transport,
                command=command,
                url=_safe_url(url) if url else "",
                args=_safe_args(_str_list(spec.get("args"))),
                env_keys=sorted(str(k) for k in env) if isinstance(env, dict) else [],
                header_keys=(
                    sorted(str(k) for k in headers) if isinstance(headers, dict) else []
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_manifest(path: str | Path) -> Manifest | None:
    """Packages and MCP servers ``path`` declares, or None when it is not a
    manifest this module reads. A malformed file yields an empty Manifest."""
    path = Path(path)
    if not is_manifest(path):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Manifest()
    lines = text.splitlines()
    out = Manifest()
    try:
        if path.name == "pyproject.toml":
            data = tomllib.loads(text)
            pkg = _python_package(path, data, lines) if isinstance(data, dict) else None
            if pkg:
                out.packages.append(pkg)
            return out
        data = _load_json(text, lenient=is_mcp_config(path))
        if not isinstance(data, dict):
            return out
        if path.name == "package.json":
            out.packages.append(_npm_package(path, data, lines))
        else:
            out.mcp_servers.extend(_mcp_servers(path, data, lines))
    except (ValueError, RecursionError, OSError):
        return Manifest()
    return out
