# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Orchestrates parsing + graph ingestion.
#              Supports full index (scan all files) and incremental update
#              (re-index a single changed file, purge stale nodes first).

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from codegraph.core.db import get_connection
from codegraph.core.fts import commit as fts_commit
from codegraph.core.fts import delete_file_symbols, get_fts_conn, upsert_symbol
from codegraph.core.protocol import GraphDB
from codegraph.core.utils import quiet_subprocess_kwargs

from .parsers import get_parser, is_supported
from .parsers.base import FileIndex

# Default Python recursion limit is 1000. Tree-sitter walks on deeply nested
# code (long method chains, big JSX trees, generated protobufs) can blow past
# that. Raise once at import time so we don't pay the cost on every call.
# Ported from graphify, same constant.
_RECURSION_LIMIT = 10_000
if sys.getrecursionlimit() < _RECURSION_LIMIT:
    sys.setrecursionlimit(_RECURSION_LIMIT)

# Keyed by resolved repo root: one owner process can touch several repos
# (federation, tests), and a single global conn would return the wrong DB.
_fts_conns: dict[str, object] = {}


def _get_fts(repo_root):
    key = str(Path(repo_root).resolve())
    conn = _fts_conns.get(key)
    if conn is None:
        conn = get_fts_conn(repo_root)
        _fts_conns[key] = conn
    return conn


_IGNORE_DIRS = {
    ".git",
    ".codegraph",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".terraform",
    "dist",
    "build",
    ".next",
}

# Per-import symbol-edge cap: above this a barrel re-export collapses to a
# single whole-module IMPORTS edge instead of one edge per named symbol.
_MAX_IMPORT_SYMBOLS = 50

_CGHIGNORE_FILE = ".cghignore"
# Keyed by resolved repo root for the same reason as _fts_conns: a global
# cache would leak one repo's patterns into another in a multi-repo process.
# Patterns are read once per repo per process; editing .cghignore needs a
# restart to take effect.
_cghignore_cache: dict[str, list[str]] = {}


def _load_cghignore(repo_root: Path) -> list[str]:
    """Load .cghignore patterns (gitignore syntax). Cached per repo root."""
    key = str(Path(repo_root).resolve())
    cached = _cghignore_cache.get(key)
    if cached is not None:
        return cached

    ignore_file = repo_root / _CGHIGNORE_FILE
    if not ignore_file.exists():
        _cghignore_cache[key] = []
        return _cghignore_cache[key]

    patterns = []
    # A .cghignore is user-authored; tolerate a non-UTF-8 byte rather than
    # crashing the scan on it.
    for line in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip negation patterns (!), we don't support them in our simple matcher
        if line.startswith("!"):
            continue
        patterns.append(line)

    _cghignore_cache[key] = patterns
    return patterns


def _is_cghignored(file_path: Path, repo_root: Path) -> bool:
    """Check if a file matches any .cghignore pattern."""
    import fnmatch

    patterns = _load_cghignore(repo_root)
    if not patterns:
        return False

    # Use forward slashes so slash-bearing patterns (e.g. "docs/*.md") match
    # on Windows too, where relative_to would otherwise yield backslashes.
    try:
        rel = file_path.relative_to(repo_root).as_posix()
    except ValueError:
        rel = file_path.as_posix()

    for pattern in patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            if any(part == dir_pattern for part in Path(rel).parts):
                return True
            continue

        # File pattern
        if fnmatch.fnmatch(rel, pattern):
            return True
        if fnmatch.fnmatch(Path(rel).name, pattern):
            return True
        # Also match against any path component
        if "/" not in pattern and any(
            fnmatch.fnmatch(part, pattern) for part in Path(rel).parts
        ):
            return True

    return False


# ---------------------------------------------------------------------------
# Graph upsert helpers
# ---------------------------------------------------------------------------


def _upsert_file(
    conn: GraphDB,
    path: str,
    lang: str,
    mtime: float,
    git_blob_sha: str | None = None,
    role: str | None = None,
    layer: str | None = None,
    module_doc: str | None = None,
) -> None:
    """MERGE a File node + SET its properties via the backend-neutral helper."""
    conn.upsert_node(
        "File",
        "path",
        path,
        {
            "lang": lang,
            "mtime": mtime,
            "git_blob_sha": git_blob_sha,
            "role": role,
            "layer": layer,
            "module_doc": module_doc,
        },
    )


def _purge_file(conn: GraphDB, path: str, fts_conn=None) -> None:
    """Delete all nodes + edges associated with a file before re-indexing it.

    Delegates the graph cleanup to the backend's purge_file_data helper,
    each backend knows its own table layout and constraint model.
    """
    conn.purge_file_data(path)
    if fts_conn is not None:
        delete_file_symbols(fts_conn, path)


def _bind_scanner_root(scanner, root) -> None:
    """Late-bind the authoritative repo root onto a scanner constructed
    rootless. The plugin registry loads once per process, and the CLI
    loads it before --root is parsed: run from outside a repo, every
    scanner would keep repo_root=None and crash on its first
    Path(repo_root) (the OneDrive/Windows report). The scan sites know
    the real root, so they repair the binding."""
    if getattr(scanner, "repo_root", "unset") is None:
        scanner.repo_root = root


def _run_scanners(root, path, idx: FileIndex, blob_sha: str | None, fts_conn) -> None:
    """Run registered inline plugin scanners on a freshly indexed file and
    queue the file for the deferred ones. A scanner failure is logged and
    never breaks indexing. Findings land in the finding store (SQLite)
    and, for searchability, in the FTS as kind="finding" rows that
    delete_file_symbols purges on the next reindex.
    """
    from codegraph.plugins import scanners as _plugin_scanners

    registered = _plugin_scanners()
    if not registered:
        return

    inline = [(n, s) for n, s in registered if not getattr(s, "deferred", False)]
    deferred = [(n, s) for n, s in registered if getattr(s, "deferred", False)]

    if deferred:
        from codegraph.state.deferred_scan import enqueue

        enqueue(root, path, blob_sha or "")

    if not inline:
        return

    # A parser that extracted text from a binary/compound format (pdf,
    # xlsx, docx) exposes it as idx.scan_text; scan THAT, not the raw
    # bytes, so PII scanners see real page/cell content and not the binary
    # noise that produces phantom card/phone hits.
    if idx.scan_text:
        text = idx.scan_text.replace("\x00", "")
    else:
        try:
            # Strip embedded nulls: binary-ish files decoded with replace
            # keep \x00, which downstream consumers (argv, SQL) reject.
            text = (
                Path(path)
                .read_text(encoding="utf-8", errors="replace")
                .replace("\x00", "")
            )
        except OSError:
            return

    from codegraph.state.findings import record_findings

    for plugin_name, scanner in inline:
        _bind_scanner_root(scanner, root)
        try:
            found = scanner.scan(Path(path), text, idx) or []
            record_findings(
                root, str(path), scanner.name, found, blob_sha=blob_sha or ""
            )
            _fts_ingest_findings(fts_conn, str(path), scanner.name, found)
        except Exception as exc:
            from codegraph.state.activity import log as _act_log

            msg = f"{path}: scanner {scanner.name} ({plugin_name}): {exc}"
            print(f"[codegraph] scan error: {msg}", file=sys.stderr, flush=True)
            _act_log(root, "scan_error", msg)


def _fts_ingest_findings(fts_conn, file_path: str, scanner: str, found: list) -> None:
    """Feed findings into the FTS so a text search can surface flagged
    files through the search tools agents already use."""
    if fts_conn is None or not found:
        return
    for f in found:
        line = int(getattr(f, "line", 0) or 0)
        upsert_symbol(
            fts_conn,
            sym_id=f"{file_path}::finding::{scanner}::{f.key}::{line}",
            kind="finding",
            name=f.key,
            file_path=file_path,
            start_line=line,
            docstring=str(f.value)[:500],
        )
    fts_commit(fts_conn)


def _fts_ingest(fts_conn, idx: FileIndex) -> None:
    """Index all symbols from a FileIndex into the FTS database."""
    if fts_conn is None:
        return
    for fn in idx.functions:
        upsert_symbol(
            fts_conn,
            sym_id=fn.id,
            kind=fn.kind,
            name=fn.name,
            file_path=fn.file_path,
            start_line=fn.start_line,
            end_line=fn.end_line,
            docstring=fn.docstring,
        )
    for cls in idx.classes:
        upsert_symbol(
            fts_conn,
            sym_id=cls.id,
            kind=cls.kind,
            name=cls.name,
            file_path=cls.file_path,
            start_line=cls.start_line,
            end_line=cls.end_line,
            docstring=cls.docstring,
        )
    for res in idx.resources:
        # Terraform blocks are found by address (var.region, module.iam,
        # google_x.y): the address is the searchable name, the summary
        # (arguments, nested blocks, a tfvars value) the text.
        kind = f"tf_{res.kind}"
        upsert_symbol(
            fts_conn,
            sym_id=res.id,
            kind=kind,
            name=res.address or res.name,
            file_path=res.file_path,
            start_line=res.start_line,
            end_line=res.end_line,
            docstring=res.docstring or res.type,
        )
    for sec in idx.sections:
        upsert_symbol(
            fts_conn,
            sym_id=sec.id,
            kind="md_section",
            name=sec.title,
            file_path=sec.file_path,
            start_line=sec.start_line,
            end_line=sec.end_line,
            docstring=sec.body_preview,
        )
    fts_commit(fts_conn)


def _call_site_rows(
    idx: FileIndex, root: Path | None
) -> tuple[list[tuple[str, ...]], frozenset[str]]:
    """The by-name call sites of a parsed file as call_site rows
    (from_id, name, "", kind, hint, ctx), and the repo files it imports;
    see analysis/call_rules.py."""
    from codegraph.analysis.call_rules import site_rows

    return site_rows(idx, root)


def _link_call_sites(
    conn: GraphDB,
    sites: list,
    root: Path | None,
    imported: dict[str, frozenset[str]],
    replace: str | None,
) -> None:
    """Create the CALLS edges of by-name ``sites`` (call_rules.Site).

    ``imported`` maps a caller file to the repo files it imports. With
    ``replace`` (a file path), ``sites`` must be every by-name site outside
    that file calling one of their names: edges from those sites into
    functions of the names that the rules no longer pick are dropped. A
    rule can depend on every function of a name (how many methods carry it,
    which file of an import defines it), so a definition appearing or
    vanishing elsewhere can take an edge away as well as add one.
    """
    if not sites:
        return
    from codegraph.analysis.call_rules import (
        CLS,
        Candidates,
        Target,
        class_bases,
        needs_imports,
        targets_for,
    )

    by_name: dict[str, list] = {}
    for fn_id, name, file_path, class_id, bases in conn.call_targets_named(
        sorted({s.name for s in sites})
    ):
        by_name.setdefault(name, []).append(
            Target(
                fn_id,
                name,
                file_path,
                class_id.rsplit("::", 1)[-1] if class_id else "",
                tuple(b.rsplit(".", 1)[-1] for b in bases),
            )
        )
    root_s = str(root) if root is not None else None
    pools = {name: Candidates(targets, root_s) for name, targets in by_name.items()}
    typed = {s.ctx for s in sites if s.kind == CLS and s.ctx and s.name in pools}
    hierarchy = class_bases(conn, typed) if typed else {}
    no_imports: frozenset[str] = frozenset()
    desired: set[tuple[str, str]] = set()
    for site in sites:
        candidates = pools.get(site.name)
        if candidates is None:
            continue
        imp = imported.get(site.file_path, no_imports)
        if not needs_imports(site.kind):
            imp = no_imports
        desired.update(
            (site.from_id, t.id)
            for t in targets_for(site, candidates, root_s, imp, hierarchy)
        )
    if replace is not None:
        existing = set(conn.calls_by_name(sorted(by_name), replace))
        stale = existing - desired
        if stale:
            conn.delete_calls(sorted(stale))
        desired -= existing
    if desired:
        conn.ensure_edges("CALLS", sorted(desired))


def _resolve_calls(
    conn: GraphDB,
    idx: FileIndex,
    root: Path | None,
    rows: list[tuple[str, ...]],
    imported: frozenset[str],
) -> None:
    """
    Create the CALLS edges of ``idx``'s call sites ``rows`` (see
    analysis/call_rules.py for the rules). Unresolved names are skipped.

    Only links to functions already in the graph: calls into a file indexed
    later are linked by that file's _resolve_inbound_calls, from the call
    sites this file recorded.
    """
    from codegraph.analysis.call_rules import Site

    if not rows:
        return
    file_of = {fn.id: fn.file_path for fn in idx.functions}
    sites = [Site(r[0], file_of[r[0]], r[1], r[3], r[4], r[5]) for r in rows]
    _link_call_sites(conn, sites, root, {str(idx.path): imported}, replace=None)


def _resolve_inbound_calls(
    conn: GraphDB,
    file_path: str,
    functions: list,
    old_names: list[str] | tuple[str, ...] = (),
    root: Path | None = None,
    classes: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Link call sites in OTHER files to the functions ``file_path`` defines.

    purge_file_data drops every CALLS edge into a file's symbols, and a
    caller indexed before this file could not see its functions at all, so
    without this pass a cross-file call depends on indexing order and is
    erased by every reindex of the callee's file. Every by-name site calling
    a name the file defines now or defined before (``old_names``) is
    resolved again with the same rules as from the caller's side, so a
    definition appearing or vanishing here also updates edges to other
    files. Precisely resolved sites (to_id set) relink only to their exact
    target, never by name. A call on a known class climbs its bases, so the
    calls on ``classes`` (the classes the file defines now or defined
    before) and on their subclasses are resolved again too.
    """
    from codegraph.analysis.call_rules import Site, needs_imports, real, subclasses

    names = {fn.name for fn in functions} | set(old_names)
    if classes:
        names.update(conn.call_site_names_on(sorted(subclasses(conn, set(classes)))))
    ids = {fn.id for fn in functions}
    if not names and not ids:
        return
    rows = conn.call_sites_into(sorted(names), sorted(ids), file_path)
    if not rows:
        return
    exact = [(r[0], r[3]) for r in rows if r[3] and r[3] in ids]
    sites = [Site(r[0], r[1], r[2], r[4], r[5], r[6]) for r in rows if not r[3]]
    callers = sorted({s.file_path for s in sites if needs_imports(s.kind)})
    imported: dict[str, set[str]] = {}
    for src, dst in conn.name_refs_from(CALLS_IMPORT, callers) if callers else ():
        imported.setdefault(src, set()).add(real(dst))
    _link_call_sites(
        conn,
        sites,
        root,
        {k: frozenset(v) for k, v in imported.items()},
        replace=file_path,
    )
    if exact:
        conn.ensure_edges("CALLS", exact)


def _delete_file_relinking(conn: GraphDB, path: str, repo_root) -> None:
    """Delete a file from the graph, then resolve again the calls elsewhere
    to the names it defined."""
    names = conn.function_names_in(path)
    classes = _class_names_in(conn, path)
    conn.delete_file_completely(path)
    _relink_calls_named(
        conn, path, names, Path(repo_root) if repo_root else None, classes
    )


def _class_names_in(conn: GraphDB, file_path: str) -> set[str]:
    """The names of the Classes ``file_path`` defines."""
    rows = conn.find_nodes(
        "Class", where={"file_path": file_path}, return_fields=["name"]
    )
    return {r["name"] for r in rows if r.get("name")}


def _relink_calls_named(
    conn: GraphDB,
    file_path: str,
    names: list[str],
    root: Path | None,
    classes: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Resolve again the call sites of ``names``, and the calls on
    ``classes``, after ``file_path`` lost its functions and classes
    (deleted, or no longer parseable)."""
    if names or classes:
        _resolve_inbound_calls(conn, file_path, [], names, root, classes)


# A name reference is (kind, from_id, name, extra), kept in the name_ref table
# by file so its edge can also be built from the target's side (see
# _resolve_inbound_refs). Kinds and what name / extra hold:
#   inherits  Class id -> base class name
#   md_ref    MdSection id -> code symbol name, extra = mention context
#   md_link   MdSection id -> resolved target path, extra = link label
#   handler   Endpoint id -> handler function name, extra = a file the route
#             file imports (one row per imported file)
#   tf_ref    Terraform block id -> "<module dir>::<address>" it uses
#   tf_modout Terraform block id -> "<module dir>::module.<m>", extra = the
#             output name (see codegraph/analysis/terraform.py)
#   tf_modres Terraform moved / import / removed block id ->
#             "<module dir>::module.<m>", extra = a resource address inside
#             that module
#   calls_import  file path -> a repo file it imports (at module level or in
#             a function body), read by the call rules of its call sites
NameRef = tuple[str, str, str, str]
CALLS_IMPORT = "calls_import"


def _keys_by_value(conn: GraphDB, label: str, field: str, values) -> dict:
    """{field value: [node keys]} for the ``label`` nodes matching ``values``."""
    out: dict[str, list[str]] = {}
    if not values:
        return out
    for key, value in conn.node_keys_matching(label, field, sorted(set(values))):
        out.setdefault(value, []).append(str(key))
    return out


def _resolve_inherits(conn: GraphDB, classes: list) -> list[NameRef]:
    """Create INHERITS edges between Class nodes using base class names.

    Links every class of the base's name already in the graph; bases defined
    in a file indexed later are linked by that file's _resolve_inbound_refs.
    Returns the references to record.
    """
    refs = sorted(
        {("inherits", cls.id, base, "") for cls in classes for base in cls.bases}
    )
    parents = _keys_by_value(conn, "Class", "name", [r[2] for r in refs])
    conn.ensure_edges(
        "INHERITS",
        [(cls_id, pid) for _, cls_id, base, _ in refs for pid in parents.get(base, ())],
    )
    return refs


def _resolve_inbound_refs(conn: GraphDB, file_path: str, idx: FileIndex) -> None:
    """Link the name references of OTHER files to what ``file_path`` defines.

    purge_file_data drops every edge into a file's symbols, and a reference
    ingested before its target existed found nothing, so without this pass
    those edges depend on indexing order and vanish when the target's file is
    reindexed. Each kind applies the same rule as its outbound resolution.
    """
    if idx.resources:
        from codegraph.analysis.terraform import resolve_inbound

        resolve_inbound(conn, file_path, idx.resources)
    fns: dict[str, list[str]] = {}
    for fn in idx.functions:
        fns.setdefault(fn.name, []).append(fn.id)
    classes: dict[str, list[str]] = {}
    for cls in idx.classes:
        classes.setdefault(cls.name, []).append(cls.id)
    refs = conn.name_refs_into(sorted({*fns, *classes, file_path}), file_path)
    if not refs:
        return
    edges: dict[str, list[tuple]] = {}
    for kind, from_id, _ref_file, name, extra in refs:
        if kind == "inherits":
            edges.setdefault("INHERITS", []).extend(
                (from_id, c) for c in classes.get(name, ())
            )
        elif kind == "md_ref":
            edges.setdefault("MD_REFS_SYMBOL", []).extend(
                (from_id, f, extra) for f in fns.get(name, ())
            )
            edges.setdefault("MD_REFS_CLASS", []).extend(
                (from_id, c, extra) for c in classes.get(name, ())
            )
        elif kind == "md_link" and name == file_path:
            edges.setdefault("MD_LINKS_TO", []).append((from_id, file_path, extra))
        elif kind == "handler" and extra == file_path:
            edges.setdefault("IMPLEMENTED_BY", []).extend(
                (from_id, f) for f in fns.get(name, ())
            )
    for edge_type, rows in edges.items():
        if rows:
            conn.ensure_edges(edge_type, rows)


def _resolve_inbound_links(conn: GraphDB, paths: list[str], exclude_file: str) -> None:
    """Land the markdown links of other files on File nodes just created."""
    if not paths:
        return
    wanted = set(paths)
    rows = [
        (from_id, name, extra)
        for kind, from_id, _f, name, extra in conn.name_refs_into(
            sorted(wanted), exclude_file
        )
        if kind == "md_link" and name in wanted
    ]
    if rows:
        conn.ensure_edges("MD_LINKS_TO", rows)


def _precise_calls_enabled(cfg, lang: str) -> bool:
    """True only when the user opted in AND jedi is importable AND this is a
    Python file. Any of these missing keeps the name-matched resolver, so the
    default install behaves exactly as before.
    """
    if cfg is None or not getattr(cfg, "precise_calls", False):
        return False
    if lang != "python":
        return False
    from codegraph.analysis.precise_calls import jedi_available

    return jedi_available()


def _resolve_calls_precise(
    conn: GraphDB, idx: FileIndex, repo_root: Path
) -> list[tuple[str, str]] | None:
    """Create CALLS edges for one Python file using the jedi-backed resolver.

    Returns the (caller_id, callee_id) pairs it resolved (possibly none) when
    it ran, None when it could not run and the caller should fall back to the
    name-matched resolver. Never raises: any error returns None so resolution
    degrades to the old path.
    """
    try:
        from codegraph.analysis.precise_calls import resolve_calls_for_file

        edges = resolve_calls_for_file(idx.path, repo_root)
    except Exception:
        return None

    dropped = 0
    for caller_id, _target_file, callee_id in edges:
        try:
            conn.ensure_edge("CALLS", caller_id, callee_id)
        except Exception:
            dropped += 1
    if dropped and repo_root is not None:
        # An incomplete call graph served as authoritative is worse than
        # a noisy log line: downstream tools (find_callers, impact_of,
        # dead code) trust these edges.
        from codegraph.state.activity import log as _act_log

        _act_log(
            repo_root, "scan_error", f"{dropped} CALLS edge(s) dropped for {idx.path}"
        )
    return [(caller_id, callee_id) for caller_id, _t, callee_id in edges]


def _ingest_code(
    conn: GraphDB,
    idx: FileIndex,
    cfg=None,
    repo_root: Path | None = None,
    pending_calls: list | None = None,
) -> list[NameRef]:
    """Ingest functions, classes, and their edges (Python, TypeScript, Vue, etc.).

    Returns the file's name references to record. The by-name call sites are
    stored here and appended to ``pending_calls`` as (rows, imported): their
    edges are created once the file's references are recorded, since the
    call rules read the base classes of the methods they may link to."""
    for fn in idx.functions:
        conn.upsert_node(
            "Function",
            "id",
            fn.id,
            {
                "name": fn.name,
                "file_path": fn.file_path,
                "start_line": fn.start_line,
                "end_line": fn.end_line,
                "docstring": fn.docstring,
            },
        )
        conn.ensure_edge("DEFINES_FN", fn.file_path, fn.id)

    for cls in idx.classes:
        conn.upsert_node(
            "Class",
            "id",
            cls.id,
            {
                "name": cls.name,
                "file_path": cls.file_path,
                "start_line": cls.start_line,
                "end_line": cls.end_line,
                "docstring": cls.docstring,
            },
        )
        conn.ensure_edge("DEFINES_CLASS", cls.file_path, cls.id)

    for fn in idx.functions:
        if fn.class_name:
            class_id = f"{fn.file_path}::{fn.class_name}"
            conn.ensure_edge("HAS_METHOD", class_id, fn.id)

    # Precise CALLS (opt-in, Python only, jedi installed). When it runs we
    # skip the name-matched resolver for this file so edges aren't doubled,
    # and record its sites by exact target so a reindex of the callee file
    # relinks them without name fan-out. Any failure or the flag being off
    # falls straight back to the old path.
    precise = None
    if repo_root is not None and _precise_calls_enabled(cfg, idx.lang):
        precise = _resolve_calls_precise(conn, idx, repo_root)
    refs = _resolve_inherits(conn, idx.classes)
    if precise is None:
        rows, imported = _call_site_rows(idx, repo_root)
        conn.replace_call_sites(str(idx.path), rows)
        refs += [(CALLS_IMPORT, str(idx.path), f, "") for f in sorted(imported)]
        if pending_calls is not None:
            pending_calls.append((rows, imported))
        else:
            _resolve_calls(conn, idx, repo_root, rows, imported)
    else:
        conn.replace_call_sites(str(idx.path), sorted({(c, "", t) for c, t in precise}))
    return refs


# Import resolution coverage for the current scan, keyed by language. Without
# it, "this file imports nothing" and "no resolver for this language" are the
# same empty answer, which is how 13 repos sat at zero import edges unnoticed.
_IMPORT_COVERAGE: dict[str, dict[str, int]] = {}
# Files the scan short-circuited as unchanged. They never reach a parser, so
# their imports never reach the counter: a measurement taken while any file
# was skipped describes part of the repo, not the repo. Counting them is what
# lets a partial run be told apart from a full one, which `indexed` cannot do
# (an unchanged file still counts as indexed).
_IMPORT_COVERAGE_SKIPPED = 0


def take_import_coverage() -> dict[str, dict[str, int]]:
    """Return the coverage gathered since the last call, and clear it."""
    snapshot = {lang: dict(counts) for lang, counts in _IMPORT_COVERAGE.items()}
    _IMPORT_COVERAGE.clear()
    return snapshot


def take_import_coverage_partial() -> bool:
    """Whether the coverage gathered since the last call missed any file."""
    global _IMPORT_COVERAGE_SKIPPED
    partial = _IMPORT_COVERAGE_SKIPPED > 0
    _IMPORT_COVERAGE_SKIPPED = 0
    return partial


def _note_unparsed_file() -> None:
    """Record that a file was skipped before reaching its parser."""
    global _IMPORT_COVERAGE_SKIPPED
    _IMPORT_COVERAGE_SKIPPED += 1


def _count_import(lang: str, resolved: bool) -> None:
    bucket = _IMPORT_COVERAGE.setdefault(lang or "unknown", {"seen": 0, "resolved": 0})
    bucket["seen"] += 1
    if resolved:
        bucket["resolved"] += 1


def _ingest_imports(conn: GraphDB, idx: FileIndex, repo_root: Path | None) -> None:
    """
    Wire IMPORTS edges from idx.imports into the graph.

    Resolves each ImportRef.source_module to a target file via
    import_resolver, then MERGEs a File → File IMPORTS edge. Unresolved
    imports (bare specifiers, missing files, third-party deps) are
    silently skipped, they're not part of the user's repo, no edge to
    draw.
    """
    if not idx.imports or repo_root is None:
        return
    from codegraph.imports.resolver import resolve_import

    seen_targets: set[str] = set()
    stubbed: set[str] = set()
    for imp in idx.imports:
        target = resolve_import(idx.lang, imp.source_module, idx.path, repo_root)
        _count_import(idx.lang, target is not None)
        if target is None:
            continue
        target_str = str(target)
        if target_str == idx.path:
            # File importing itself, skip the self-loop.
            continue

        # Make sure the target File exists. During incremental indexing
        # the importer may be processed before its dependency, so upsert
        # creates a stub node carrying just the path. Once the target's
        # own index_file runs, the same key gets upserted with full metadata.
        conn.upsert_node("File", "path", target_str, {})
        stubbed.add(target_str)

        # Symbol annotation on the edge. If the import named multiple
        # symbols, write one edge per symbol so MCP tools can answer
        # "who imports name X". Single edge with empty symbol when the
        # import is a whole-module pull. A barrel re-export can name
        # hundreds of symbols, so collapse past a cap to one whole-module
        # edge rather than flooding the graph with per-symbol edges.
        symbols = imp.symbols if imp.symbols else [""]
        if len(symbols) > _MAX_IMPORT_SYMBOLS:
            symbols = [""]
        for sym in symbols:
            edge_key = f"{target_str}::{sym}"
            if edge_key in seen_targets:
                continue
            seen_targets.add(edge_key)
            conn.ensure_edge("IMPORTS", idx.path, target_str, {"symbol": sym})
    # A stub can be the only File node a path ever gets (a target cgh does not
    # parse), so markdown links waiting for it must land now, as they would
    # had the doc been ingested after this file.
    _resolve_inbound_links(conn, sorted(stubbed), str(idx.path))


def _handler_candidate_files(idx: FileIndex, repo_root: Path | None) -> list[str]:
    """Files a route file imports, where a handler it names may live.

    Every resolvable import target, plus for Python each imported name tried
    as a submodule (``from pkg import views`` resolves to pkg/__init__.py,
    while ``views.user_detail`` lives in pkg/views.py).
    """
    if not idx.imports or repo_root is None:
        return []
    from codegraph.imports.resolver import resolve_import

    targets: set[str] = set()
    for imp in idx.imports:
        modules = [imp.source_module]
        if idx.lang == "python":
            sep = "" if imp.source_module.endswith(".") else "."
            modules += [f"{imp.source_module}{sep}{sym}" for sym in imp.symbols]
        for module in modules:
            target = resolve_import(idx.lang, module, idx.path, repo_root)
            if target is not None:
                targets.add(str(target))
    targets.discard(str(idx.path))
    return sorted(targets)


def _ingest_terraform(
    conn: GraphDB, idx: FileIndex, cfg=None, root: Path | None = None
) -> list[NameRef]:
    """Ingest a file's Terraform blocks and link the addresses they use.

    Module sources resolve through the opt-in [terraform] module_sources
    mapping; a mapped module directory no index covers has its variables
    and outputs read (read-only) into this graph first, so the call links.
    Returns the name references to record, so a block defined in a file
    indexed later, or reindexed, links itself back (_resolve_inbound_refs).
    """
    from codegraph.analysis import terraform as _tf

    sources = _tf.ModuleSources.from_config(cfg, root)
    _tf.ingest_blocks(conn, idx.resources, sources)
    for directory in _tf.external_module_dirs(idx.resources, sources):
        _tf.ingest_external_module(conn, directory, sources)
    refs = _tf.ref_rows(idx.resources, sources)
    _tf.resolve_outbound(conn, refs)
    return refs


def _ingest_endpoints(
    conn: GraphDB,
    path: Path,
    idx: FileIndex | None = None,
    repo_root: Path | None = None,
    refs: list[NameRef] | None = None,
) -> int:
    """Extract and persist HTTP endpoints from a file. Returns count.

    A handler is linked in the route's own file first. Only when that file
    defines no function of the handler's name (a Django urls.py naming
    views.user_detail), it is looked up in the files the route file imports
    (``idx`` given), never across the whole repo: a bare name like ``detail``
    or ``index`` is defined in many unrelated modules. Those lookups are
    appended to ``refs`` so a handler file indexed later, or reindexed, links
    itself back.
    """
    from codegraph.analysis.endpoints import extract as _extract_endpoints

    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    eps = _extract_endpoints(path, src)
    if not eps:
        return 0

    # purge_file_data already cleaned old endpoints for this path during
    # the upstream _purge_file call, so no separate purge needed here.

    local: set[str] = set()
    for ep in eps:
        conn.upsert_node(
            "Endpoint",
            "id",
            ep.id,
            {
                "method": ep.method,
                "path": ep.path,
                "framework": ep.framework,
                "file_path": ep.file_path,
                "start_line": ep.start_line,
            },
        )
        conn.ensure_edge("DEFINES_ENDPOINT", str(path), ep.id)
        if ep.handler_name:
            # Link to the handler Function in this same file. Use the
            # backend's find_node_keys + ensure_edge so name resolution
            # works on both DuckDB and SQLite.
            for fn_id in conn.find_node_keys("Function", "name", ep.handler_name):
                # find_node_keys returns *all* matches; filter to this file
                # by checking the id prefix (id = file_path + '::' + name).
                # A node id is TEXT by schema, but an older or drifted
                # graph.duckdb can hand back a non-str key here (seen as an
                # INT32 when re-indexing an existing index), so coerce before
                # the prefix check instead of crashing the whole reindex on it.
                fn_id = str(fn_id)
                if fn_id.startswith(f"{path}::") or f"::{path}::" in fn_id:
                    conn.ensure_edge("IMPLEMENTED_BY", ep.id, fn_id)
                    local.add(ep.id)
    if idx is not None and refs is not None:
        remote = [ep for ep in eps if ep.handler_name and ep.id not in local]
        _link_remote_handlers(
            conn, remote, _handler_candidate_files(idx, repo_root), refs
        )
    return len(eps)


def _link_remote_handlers(
    conn: GraphDB, eps: list, candidates: list[str], refs: list[NameRef]
) -> None:
    """Link endpoints to handlers defined in ``candidates`` (the files the
    route file imports) and record one reference per endpoint and file."""
    if not eps or not candidates:
        return
    wanted = set(candidates)
    defs: dict[str, list[str]] = {}
    for fn_id, name, file_path in conn.function_defs_named(
        sorted({ep.handler_name for ep in eps})
    ):
        if file_path in wanted:
            defs.setdefault(name, []).append(str(fn_id))
    edges: list[tuple[str, str]] = []
    for ep in eps:
        edges.extend((ep.id, fn_id) for fn_id in defs.get(ep.handler_name, ()))
        refs.extend(("handler", ep.id, ep.handler_name, f) for f in candidates)
    conn.ensure_edges("IMPLEMENTED_BY", edges)


def _ingest_markdown(conn: GraphDB, idx: FileIndex) -> list[NameRef]:
    """Ingest a doc's sections and its links / code mentions. Returns the
    name references to record."""
    # Sections
    for sec in idx.sections:
        conn.upsert_node(
            "MdSection",
            "id",
            sec.id,
            {
                "title": sec.title,
                "level": sec.level,
                "file_path": sec.file_path,
                "start_line": sec.start_line,
                "end_line": sec.end_line,
                "body_preview": sec.body_preview,
                "anchor": sec.anchor,
                "kind": getattr(sec, "kind", "doc"),
            },
        )
        conn.ensure_edge("DEFINES_SECTION", sec.file_path, sec.id)

    # Section hierarchy: parent contains child when child.level > parent.level
    # and child comes before the next same-or-higher-level section
    for i, parent in enumerate(idx.sections):
        for j in range(i + 1, len(idx.sections)):
            child = idx.sections[j]
            if child.level <= parent.level:
                break
            if child.level == parent.level + 1:
                conn.ensure_edge("CONTAINS_SECTION", parent.id, child.id)

    # Internal links: link markdown sections to files they reference.
    # Markdown links are written relative to the file that contains them, so
    # resolve each target against this file's directory before matching the
    # (absolute) File node path. This makes ./foo.md and ../api.md resolve,
    # where the old raw exact-match on "./foo.md" never did.
    # A link to a file indexed later lands from that file's side, through
    # the md_link reference recorded here (_resolve_inbound_refs).
    md_dir = os.path.dirname(idx.path)
    refs: set[NameRef] = set()
    for link in idx.links:
        target = link.target
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path = target.split("#")[0]
        if not target_path:
            continue
        resolved_target = os.path.normpath(os.path.join(md_dir, target_path))
        section = _find_section_for_line(idx.sections, link.line)
        if not section:
            continue
        refs.add(("md_link", section.id, resolved_target, link.label or ""))

    # Code references: link sections to the functions and classes they
    # mention, by name; a symbol defined in a file indexed later links back
    # through the md_ref reference.
    for ref in idx.code_refs:
        section = _find_section_for_line(idx.sections, ref.line)
        if not section:
            continue
        refs.add(("md_ref", section.id, ref.symbol, ref.context or ""))

    links = [r for r in refs if r[0] == "md_link"]
    files = _keys_by_value(conn, "File", "path", [r[2] for r in links])
    conn.ensure_edges(
        "MD_LINKS_TO",
        [
            (sec, key, label)
            for _, sec, target, label in links
            for key in files.get(target, ())
        ],
    )
    mentions = [r for r in refs if r[0] == "md_ref"]
    names = [r[2] for r in mentions]
    for edge_type, label in (
        ("MD_REFS_SYMBOL", "Function"),
        ("MD_REFS_CLASS", "Class"),
    ):
        found = _keys_by_value(conn, label, "name", names)
        conn.ensure_edges(
            edge_type,
            [
                (sec, key, ctx)
                for _, sec, name, ctx in mentions
                for key in found.get(name, ())
            ],
        )
    return sorted(refs)


def _find_section_for_line(sections: list, line: int):
    """Find the deepest (most specific) section containing a given line."""
    best = None
    for sec in sections:
        if sec.start_line <= line <= sec.end_line and (
            best is None or sec.level > best.level
        ):
            best = sec
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def index_file(
    path: str | Path,
    repo_root: str | Path | None = None,
    force: bool = False,
    git_blob_sha: str | None = None,
    cfg=None,
    reparse: bool = False,
) -> bool:
    """
    Parse and ingest a single file into the graph.
    Returns True on success, False if the file type is unsupported or parse fails.

    Args:
        path: File to index.
        repo_root: Repository root (default: CWD).
        force: If True, index even if the file is in .gitignore or .git/info/exclude.
               Skips mtime cache check too, always re-parses.
        cfg: Pre-loaded CodegraphConfig. index_repo passes one so the size /
             ignore-pattern gate doesn't re-read config.toml per file. When
             None (standalone callers) it is loaded once for this call.
        reparse: Skip only the mtime cache check (unlike force, the ignore
             rules still apply). Used by a full index after a graph format
             upgrade, when unchanged files still need their new data.
    """
    from codegraph.state.index_lock import write_lock

    with write_lock(repo_root or Path.cwd()):
        return _index_file(path, repo_root, force, git_blob_sha, cfg, reparse)


def _index_file(
    path: str | Path,
    repo_root: str | Path | None,
    force: bool,
    git_blob_sha: str | None,
    cfg,
    reparse: bool = False,
) -> bool:
    path = Path(path)
    suffix = path.suffix.lower()
    parser = get_parser(suffix)
    if parser is None:
        from .parsers import get_parser_for_path

        parser = get_parser_for_path(path)
    if parser is None:
        return False

    # Check .cghignore (skip if force)
    root = Path(repo_root) if repo_root else Path.cwd()
    if not force and _is_cghignored(path, root):
        return False

    # Respect the configured size cap + ignore_patterns (skip if force). These
    # were defined and documented but never enforced, so a huge minified or
    # generated file would still be fully read and tree-sitter parsed.
    if not force:
        import fnmatch as _fnmatch

        from codegraph.core.config import load_config

        _cfg = cfg if cfg is not None else load_config(root)
        if any(_fnmatch.fnmatch(path.name, pat) for pat in _cfg.ignore_patterns):
            return False
        try:
            if path.stat().st_size > _cfg.max_file_size_kb * 1024:
                return False
        except OSError:
            pass

    conn = get_connection(repo_root)
    mtime = path.stat().st_mtime

    # Check if already indexed and unchanged (skip if force or reparse)
    if not force and not reparse:
        try:
            stored_mtime = conn.query_node_field("File", "path", str(path), "mtime")
            if stored_mtime is not None and abs(float(stored_mtime) - mtime) < 0.01:
                _note_unparsed_file()
                return True  # unchanged
        except Exception:
            pass

    fts_conn = _get_fts(repo_root) if repo_root else None
    # The names this file defined: callers of those names elsewhere are
    # resolved again once the file is back in (or gone).
    old_names = conn.function_names_in(str(path))
    old_classes = _class_names_in(conn, str(path))
    _purge_file(conn, str(path), fts_conn)

    try:
        idx = parser.parse(path)
    except RecursionError:
        # Tree-sitter walk on extremely nested ASTs can recurse past even our
        # raised limit. Skip the file cleanly so the rest of the scan continues.
        from codegraph.state.activity import log as _act_log

        msg = f"{path}: recursion_limit_exceeded (depth > {_RECURSION_LIMIT})"
        print(f"[codegraph] parse skipped: {msg}", file=sys.stderr, flush=True)
        _act_log(root, "parse_error", msg)
        _relink_calls_named(conn, str(path), old_names, root, old_classes)
        return False
    except Exception as exc:
        # Catch-all: any other parse failure (decoding error, malformed source,
        # tree-sitter binding bug, ...) skips this one file instead of taking
        # down the whole scan.
        from codegraph.state.activity import log as _act_log

        msg = f"{path}: {type(exc).__name__}: {exc}"
        print(f"[codegraph] parse error: {msg}", file=sys.stderr, flush=True)
        _act_log(root, "parse_error", msg)
        _relink_calls_named(conn, str(path), old_names, root, old_classes)
        return False

    lang = idx.lang

    # Compute git blob SHA for surgical-reindex detection. Caller can pass
    # it in (batched lookup) or we fall back to a per-file subprocess.
    blob_sha = git_blob_sha
    if blob_sha is None:
        try:
            from codegraph.state.scan_meta import git_hash_object

            blob_sha = git_hash_object(root, path)
        except Exception:
            pass

    # Role + layer classification + module-level summary for arch tools
    from codegraph.analysis.module_doc import extract as _extract_doc
    from codegraph.analysis.roles import classify as _classify_role

    role, layer = _classify_role(path, root)
    module_doc = _extract_doc(path, lang)

    _upsert_file(
        conn,
        str(path),
        lang,
        mtime,
        git_blob_sha=blob_sha,
        role=role,
        layer=layer,
        module_doc=module_doc,
    )

    # Resolve the effective config once for ingest. index_repo threads its
    # pre-loaded cfg in; standalone / force callers get a fresh load. Only
    # consulted for the opt-in precise_calls flag below, so the cost is paid
    # only when something actually reads it.
    if cfg is not None:
        eff_cfg = cfg
    else:
        from codegraph.core.config import load_config as _load_config_for_ingest

        eff_cfg = _load_config_for_ingest(root)

    # Ingest into graph
    refs: list[NameRef] = []
    pending_calls: list = []
    if idx.functions or idx.classes:
        refs += _ingest_code(
            conn, idx, cfg=eff_cfg, repo_root=root, pending_calls=pending_calls
        )
    if idx.resources:
        refs += _ingest_terraform(conn, idx, eff_cfg, root)
    if idx.sections:
        refs += _ingest_markdown(conn, idx)

    # IMPORTS edges, wire them up after the File node exists, regardless
    # of whether the file defines functions/classes (pure __init__.py
    # re-export modules still have meaningful imports).
    if idx.imports:
        _ingest_imports(conn, idx, root)

    # HTTP endpoints (after functions are in place so IMPLEMENTED_BY can link)
    _ingest_endpoints(conn, path, idx=idx, repo_root=root, refs=refs)

    # Keep this file's by-name references, then link the references other
    # files hold to what this one defines (purge_file_data cleared both).
    if refs:
        conn.replace_name_refs(str(path), refs)
    _resolve_inbound_refs(conn, str(path), idx)

    # CALLS last: the call rules read the recorded base classes and imports.
    # Then the calls of other files to the names this file defines now or
    # defined before.
    for rows, imported in pending_calls:
        _resolve_calls(conn, idx, root, rows, imported)
    _resolve_inbound_calls(
        conn,
        str(path),
        idx.functions,
        old_names,
        root,
        old_classes | {c.name for c in idx.classes},
    )

    # Ingest into FTS
    _fts_ingest(fts_conn, idx)

    # Plugin scanners: inline tier runs now, deferred tier gets queued
    _run_scanners(root, path, idx, blob_sha, fts_conn)

    # Last, so a run killed halfway leaves the file unstamped and the next
    # index parses it again (see _reparse_unstamped).
    conn.stamp_file(str(path), mtime)
    return True


def _reparse_unstamped(repo_root: Path, activity_log) -> list[str]:
    """Parse again the files an older indexer wrote since this one last ran.

    cgh 0.15 (after a rollback) rewrites a file's nodes and edges but keeps
    no call sites, name references or stamp, and its purge leaves the ones
    this format recorded. Without this pass the next run trusts the mtime and
    blob sha 0.15 stored and serves the stale references: edges into the file
    stay lost and its old call sites relink removed calls. Re-indexing the
    file rebuilds both directions; references of files 0.15 deleted are
    dropped. Returns the paths parsed again.
    """
    conn = get_connection(repo_root)
    try:
        stale, orphans = conn.unstamped_files()
    except Exception as exc:
        activity_log(repo_root, "scan_error", f"stamp check failed: {exc}")
        return []
    for path in orphans:
        conn.purge_file_data(path)
    fts_conn = _get_fts(repo_root)
    done: list[str] = []
    for path in stale:
        p = Path(path)
        try:
            if not p.exists():
                _delete_file_relinking(conn, path, repo_root)
                if fts_conn is not None:
                    delete_file_symbols(fts_conn, path)
                continue
            if index_file(p, repo_root, force=True):
                done.append(path)
                continue
            # Not indexable by this version: keep what is there, and stamp it
            # so it is not retried on every run.
            mtime = conn.query_node_field("File", "path", path, "mtime")
            if mtime is not None:
                conn.stamp_file(path, float(mtime))
        except Exception as exc:
            activity_log(repo_root, "scan_error", f"re-parse failed for {path}: {exc}")
    if stale or orphans:
        activity_log(
            repo_root,
            "older_writer_repair",
            f"reparsed={len(done)} stale={len(stale)} orphans={len(orphans)}",
        )
    return done


def older_writer_pending(repo_root: str | Path) -> bool:
    """True when files written by an older indexer are waiting for
    _reparse_unstamped. Errors count as False: this only decides whether an
    owner indexes on start."""
    try:
        stale, orphans = get_connection(repo_root).unstamped_files()
    except Exception:
        return False
    return bool(stale or orphans)


def _git_tracked_files(repo_root: Path) -> list[Path] | None:
    """
    Use `git ls-files` to get tracked + untracked-not-ignored files.
    Also filters out _IGNORE_DIRS (node_modules, .venv, etc.) as extra safety.
    Files inside any federated subrepo path are skipped, those repos
    own their own index and the parent acts as a passe-plat for them.
    Returns None if not a git repo or git command fails (fallback to os.walk).
    """
    import subprocess

    from codegraph.analysis.federation import child_paths_to_skip, is_under_any

    subrepos = child_paths_to_skip(repo_root)

    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(repo_root),
            timeout=30,
            **quiet_subprocess_kwargs(),
        )
        if result.returncode != 0:
            return None
        files = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip files inside ignored directories
            parts = Path(line).parts
            if any(part in _IGNORE_DIRS or part.startswith(".") for part in parts):
                continue
            # Skip files matching .cghignore
            full = repo_root / line
            if _is_cghignored(full, repo_root):
                continue
            # Skip files inside a federated subrepo
            if subrepos and is_under_any(full, subrepos):
                continue
            files.append(full)
        # Merge in include_dirs (force-index even when gitignored)
        files.extend(_walk_include_dirs(repo_root, seen=set(files)))
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def resolve_include_dirs_safe(repo_root: Path) -> list[Path]:
    try:
        from codegraph.core.config import resolve_include_dirs

        return resolve_include_dirs(repo_root)
    except Exception:
        return []


def _walk_include_dirs(repo_root: Path, seen: set[Path] | None = None) -> list[Path]:
    """Walk configured include_dirs (config.toml) and return files not yet seen.
    Files inside any federated subrepo are skipped."""
    from codegraph.analysis.federation import child_paths_to_skip, is_under_any
    from codegraph.core.config import resolve_include_dirs

    seen = seen or set()
    out: list[Path] = []
    try:
        include_dirs = resolve_include_dirs(repo_root)
    except Exception:
        return out
    subrepos = child_paths_to_skip(repo_root)
    for base in include_dirs:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")
            ]
            for filename in filenames:
                p = Path(dirpath) / filename
                if p in seen:
                    continue
                if _is_cghignored(p, repo_root):
                    continue
                if subrepos and is_under_any(p, subrepos):
                    continue
                out.append(p)
                seen.add(p)
    return out


VALID_METHODS = ("auto", "git_ls_files", "os_walk", "find", "git_diff", "incremental")


def _discover_find(repo_root: Path) -> list[Path]:
    """Use GNU `find -type f` for file discovery (fast on large repos)."""
    import subprocess

    # Prune the heavy ignore dirs at the walk level so find doesn't descend
    # into node_modules/.venv/etc.; the post-hoc _IGNORE_DIRS check below stays
    # as a backstop for anything the prune misses.
    prune: list[str] = []
    for d in sorted(_IGNORE_DIRS):
        prune += ["-name", d, "-o"]
    prune = prune[:-1]  # drop the trailing -o
    cmd = [
        "find",
        str(repo_root),
        "(",
        "-type",
        "d",
        "(",
        *prune,
        ")",
        "-prune",
        ")",
        "-o",
        "(",
        "-type",
        "f",
        "-not",
        "-path",
        "*/.*",
        "-print",
        ")",
    ]

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **quiet_subprocess_kwargs(),
        )
        if r.returncode != 0:
            return []
        out: list[Path] = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if any(part in _IGNORE_DIRS for part in p.parts):
                continue
            if _is_cghignored(p, repo_root):
                continue
            out.append(p)
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _discover_os_walk(repo_root: Path) -> list[Path]:
    """Python os.walk, portable, respects _IGNORE_DIRS + .cghignore + federated subrepos."""
    from codegraph.analysis.federation import child_paths_to_skip, is_under_any

    subrepos = child_paths_to_skip(repo_root)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")
        ]
        # Prune subrepo directories so we don't even descend into them
        if subrepos:
            dirnames[:] = [
                d for d in dirnames if not is_under_any(Path(dirpath) / d, subrepos)
            ]
        for filename in filenames:
            p = Path(dirpath) / filename
            if _is_cghignored(p, repo_root):
                continue
            if subrepos and is_under_any(p, subrepos):
                continue
            out.append(p)
    return out


def _discover_git_diff(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """
    Return (changed_files, deleted_files) since the last scan (from scan_meta).
    Falls back to an empty list when no prior scan exists, caller should
    switch to a full method in that case.
    """
    import subprocess

    from codegraph.state.scan_meta import read_meta

    meta = read_meta(repo_root)
    if not meta or not meta.get("git_head"):
        return [], []
    last_sha = meta["git_head"]
    try:
        # Committed changes
        r = subprocess.run(
            ["git", "diff", "--name-status", f"{last_sha}..HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(repo_root),
            # Match git ls-files (30s): a large rebase diff was timing out at
            # 10s and silently falling back to a full scan.
            timeout=30,
            **quiet_subprocess_kwargs(),
        )
        changed: list[Path] = []
        deleted: list[Path] = []
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status, path = parts[0].strip(), parts[-1].strip()
                full = repo_root / path
                if status.startswith("D"):
                    deleted.append(full)
                else:
                    changed.append(full)
        return changed, deleted
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return [], []


def _discover_candidates(
    repo_root: Path, method: str
) -> tuple[list[Path], list[Path], str]:
    """Run the requested discovery strategy with its documented
    fallbacks. Returns (candidates, deletions, actual_method)."""
    actual_method = method
    candidates: list[Path] = []
    deletions: list[Path] = []

    if method in ("auto", "git_ls_files"):
        git_files = _git_tracked_files(repo_root)
        if git_files is None:
            if method == "git_ls_files":
                raise RuntimeError(
                    "git_ls_files requested but git is unavailable or repo not initialised"
                )
            # auto -> fall back
            actual_method = "os_walk"
            candidates = _discover_os_walk(repo_root)
        else:
            actual_method = "git_ls_files"
            candidates = list(git_files)

    elif method == "os_walk":
        candidates = _discover_os_walk(repo_root)

    elif method == "find":
        candidates = _discover_find(repo_root)
        if not candidates:
            # Tool missing or errored, fall back
            actual_method = "os_walk"
            candidates = _discover_os_walk(repo_root)

    elif method == "git_diff":
        candidates, deletions = _discover_git_diff(repo_root)
        if not candidates and not deletions:
            # No prior scan meta -> do a full scan instead
            actual_method = "git_ls_files"
            git_files = _git_tracked_files(repo_root)
            candidates = (
                list(git_files)
                if git_files is not None
                else _discover_os_walk(repo_root)
            )
            if git_files is None:
                actual_method = "os_walk"

    return candidates, deletions, actual_method


def _filter_parseable(candidates: list[Path], scan_cfg, stats: dict) -> list[Path]:
    """Keep supported, existing, non-ignored files under the size cap;
    every rejection counts as skipped."""
    import fnmatch as _fnmatch

    size_cap = scan_cfg.max_file_size_kb * 1024
    parseable: list[Path] = []
    for p in candidates:
        if not is_supported(p):
            stats["skipped"] += 1
            continue
        if any(part in _IGNORE_DIRS for part in p.parts):
            stats["skipped"] += 1
            continue
        if not p.exists():
            stats["skipped"] += 1
            continue
        if any(_fnmatch.fnmatch(p.name, pat) for pat in scan_cfg.ignore_patterns):
            stats["skipped"] += 1
            continue
        try:
            if p.stat().st_size > size_cap:
                stats["skipped"] += 1
                continue
        except OSError:
            pass
        parseable.append(p)
    return parseable


def _delete_gone(repo_root: Path, deletions: list[Path], activity_log) -> None:
    """Purge files git_diff reported as deleted; a failed delete is a
    ghost file and is logged, never swallowed."""
    fts_conn = _get_fts(repo_root)
    conn = get_connection(repo_root)
    for gone in deletions:
        try:
            _delete_file_relinking(conn, str(gone), repo_root)
            if fts_conn is not None:
                delete_file_symbols(fts_conn, str(gone))
            from codegraph.state.findings import purge_file_findings

            purge_file_findings(repo_root, str(gone))
        except Exception as exc:
            activity_log(repo_root, "scan_error", f"delete {gone}: {exc}")


def _purge_fts_orphans(repo_root: Path, activity_log) -> int:
    """Drop the FTS rows of files the graph no longer holds.

    Deletions are driven by the graph's File nodes, so a file that left the
    graph without its FTS rows (a graph rebuilt while fts.db was kept: a
    backend migration, a corrupt-graph recovery) was never looked at again.
    Its symbols stayed searchable for good, and `init --from` copied them into
    every seeded checkout. Returns the number of files purged.
    """
    fts_conn = _get_fts(repo_root)
    if fts_conn is None:
        return 0
    try:
        in_graph = {
            path
            for (path,) in get_connection(repo_root).list_node_fields("File", ["path"])
        }
    except Exception as exc:
        activity_log(repo_root, "scan_error", f"fts reconcile skipped: {exc}")
        return 0
    if not in_graph:
        return 0  # an empty graph proves nothing, never wipe the FTS on it
    in_fts = {
        path for (path,) in fts_conn.execute("SELECT DISTINCT file_path FROM symbols")
    }
    orphans = sorted(in_fts - in_graph)
    if not orphans:
        return 0
    from codegraph.state.findings import purge_file_findings

    for path in orphans:
        try:
            delete_file_symbols(fts_conn, path)
            purge_file_findings(repo_root, path)
        except Exception as exc:
            activity_log(repo_root, "scan_error", f"fts reconcile {path}: {exc}")
    fts_commit(fts_conn)
    activity_log(
        repo_root, "fts_reconciled", f"{len(orphans)} file(s) not in the graph"
    )
    return len(orphans)


def _index_extra_dirs(
    repo_root: Path,
    stats: dict,
    activity_log,
    reparse: bool = False,
    tf_reparse: bool = False,
) -> list[str]:
    """Index the sibling directories declared in config.toml. A
    malformed config must not silently shrink coverage."""
    extra_dirs: list[str] = []
    try:
        import tomllib

        cfg = repo_root / ".codegraph" / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                cfg_data = tomllib.load(f)
            extra_dirs = cfg_data.get("codegraph", {}).get("extra_dirs", [])
    except Exception as exc:
        print(f"  ! config.toml unreadable, extra_dirs skipped: {exc}")
        activity_log(
            repo_root,
            "scan_error",
            f"config.toml unreadable, extra_dirs skipped: {exc}",
        )

    for rel in extra_dirs:
        extra_root = (repo_root / rel).resolve()
        if not extra_root.exists() or not extra_root.is_dir():
            continue
        activity_log(repo_root, "extra_dir_scan", str(extra_root))
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(extra_root):
            dirnames[:] = [
                d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")
            ]
            for filename in filenames:
                full_path = Path(dirpath) / filename
                if is_supported(full_path):
                    files.append(full_path)
        # One git run hashes them all; index_file would spawn one per file.
        from codegraph.state.scan_meta import git_hash_objects

        shas = git_hash_objects(repo_root, files)
        for full_path in files:
            try:
                ok = index_file(
                    full_path,
                    repo_root,
                    git_blob_sha=shas.get(str(full_path)),
                    reparse=reparse or (tf_reparse and _is_terraform(full_path)),
                )
                if ok:
                    stats["indexed"] += 1
                else:
                    stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1
    return extra_dirs


def _is_terraform(path: Path | str) -> bool:
    return str(path).endswith((".tf", ".tfvars"))


def _module_sources_reparse(repo_root: Path, cfg, reparse: bool, activity_log) -> bool:
    """Whether this run parses the Terraform files again because the
    [terraform] module_sources mapping, or a commit one of its pinned refs
    resolves to, changed since the last scan (scan_meta keeps the
    fingerprint). Then, and on a full re-parse, the module blocks read from
    outside the index are dropped first so they are read again under the
    current mapping. Errors count as no change."""
    from codegraph.analysis import terraform as _tf

    try:
        changed = not reparse and module_sources_changed(repo_root, cfg)
        if changed or reparse:
            dropped = _tf.purge_external_modules(get_connection(repo_root))
            if changed:
                activity_log(
                    repo_root,
                    "module_sources_changed",
                    f"re-parsing Terraform files, dropped {dropped} module files",
                )
        return changed
    except Exception as exc:
        activity_log(repo_root, "scan_error", f"module_sources check failed: {exc}")
        return False


def module_sources_changed(repo_root: str | Path, cfg=None) -> bool:
    """True when the module_sources fingerprint scan_meta recorded differs
    from the current one (mapping edited, a pinned ref moved, appeared or
    vanished). No scan record counts as unchanged: a first index parses
    everything anyway."""
    from codegraph.analysis.terraform import module_sources_fingerprint
    from codegraph.core.config import load_config
    from codegraph.state.scan_meta import read_meta

    meta = read_meta(repo_root)
    if meta is None:
        return False
    stored = meta.get("module_sources") or {}
    if cfg is None:
        cfg = load_config(repo_root)
    current = module_sources_fingerprint(cfg, repo_root, stored.get("pinned") or [])
    return current != (stored.get("fingerprint") or "")


def _record_module_sources(repo_root: Path, activity_log) -> None:
    """Store what module_sources resolves to now in scan_meta (fingerprint,
    pinned refs, per mapping the refs found and missing), and log a
    warning per missing ref."""
    from codegraph.analysis import terraform as _tf
    from codegraph.core.config import load_config
    from codegraph.state.scan_meta import record_module_sources

    try:
        state = _tf.module_sources_report(
            get_connection(repo_root), load_config(repo_root), repo_root
        )
        record_module_sources(repo_root, state)
        for notice in _tf.module_sources_notices(state):
            activity_log(repo_root, "module_sources_ref_missing", notice)
    except Exception as exc:
        activity_log(repo_root, "scan_error", f"module_sources report failed: {exc}")


def _is_graph_corrupt(exc: BaseException) -> bool:
    """A graph-DB error that means the on-disk index is inconsistent and the
    graph must be rebuilt, not a transient failure to retry as-is. DuckDB
    reports a corrupt ART index as 'Failed to delete all rows from index'
    and, once the connection is poisoned, 'database has been invalidated'."""
    m = str(exc).lower()
    return (
        "failed to delete all rows from index" in m
        or "database has been invalidated" in m
    )


def _recover_corrupt_graph(repo_root: Path) -> None:
    """Drop the poisoned cached connection and delete the graph DB files so
    the next open builds a fresh graph. The graph is fully derived from
    source, so this loses only the index: FTS, knowledge and config stay."""
    from codegraph.core.db import get_db_path, reset_connection
    from codegraph.state.activity import log as _activity_log

    reset_connection(repo_root)
    try:
        db = get_db_path(repo_root)
        for p in db.parent.glob(db.name + "*"):  # graph.duckdb, .wal, .tmp
            p.unlink(missing_ok=True)
        _activity_log(repo_root, "graph_rebuilt", f"corrupt graph wiped: {db.name}")
    except OSError:
        pass


_FATAL_GRAPH_ERROR = re.compile(
    r"^(?:[A-Za-z]+(?: [A-Za-z]+)? Error: ){0,3}"
    r"(?:Failed to delete all rows from index\."
    r"|(?:Failed: )?database has been invalidated because of a previous fatal error)",
    re.IGNORECASE,
)
_REBUILD_COOLDOWN_S = 300.0
_last_rebuild: dict[str, float] = {}


def is_fatal_graph_error(exc: BaseException) -> bool:
    """True only for an error raised by DuckDB itself whose message STARTS
    with one of its two corruption reports.

    Stricter than _is_graph_corrupt on purpose. That one matches a substring
    of any exception text, which is fine inside a reindex the user asked for,
    but this result wipes the graph on its own: an unrelated error that merely
    quotes such a sentence (a file named after it, a tool argument echoed in
    a message) must never count. So the exception has to be a duckdb.Error,
    here or in its cause chain, and the text has to match from the start."""
    try:
        import duckdb
    except ImportError:
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, duckdb.Error) and _FATAL_GRAPH_ERROR.match(
            str(current).lstrip()
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def rebuild_corrupt_graph(repo_root: str | Path, exc: BaseException) -> dict | None:
    """Rebuild the graph after a corruption error met outside a reindex: the
    file watcher re-indexing a saved file, or an MCP tool reading the graph.

    index_repo and incremental_reindex already recover on their own, but a
    fatal DuckDB error on those two paths left the connection poisoned, so
    every later call failed until the owner was restarted, and the stored
    scan metadata went on calling the index fresh. Here the graph is wiped,
    the scan metadata is dropped (the index is not fresh until the rebuild
    writes it again) and the repo is re-indexed in full.

    Returns the index stats, or None when ``exc`` is not a fatal DuckDB error
    (see is_fatal_graph_error), when this repo was rebuilt less than five
    minutes ago, or when another index run already holds the repo."""
    if not is_fatal_graph_error(exc):
        return None
    from codegraph.state.activity import log as _activity_log
    from codegraph.state.index_lock import IndexBusy, index_lock
    from codegraph.state.scan_meta import clear_meta

    root = Path(repo_root)
    key = str(root.resolve())
    now = time.time()
    last = _last_rebuild.get(key)
    if last is not None and now - last < _REBUILD_COOLDOWN_S:
        # Just rebuilt: do not loop on an error that keeps coming. Still drop
        # the poisoned connection so the next call reopens the database.
        from codegraph.core.db import reset_connection

        reset_connection(root)
        return None
    _last_rebuild[key] = now
    try:
        with index_lock(root):
            _activity_log(root, "graph_corrupt_recover", str(exc)[:200])
            _recover_corrupt_graph(root)
            clear_meta(root)
            return _index_repo(root, method="os_walk")
    except IndexBusy:
        return None


def _rel_or_abs(path: Path, root: Path) -> str:
    """``path`` relative to ``root``, or absolute when outside it: the key
    the index loop looks a blob SHA up by."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _foreign_root(repo_root: Path) -> str:
    """Describe why the stored graph belongs to another root, or return "".

    The scan metadata records the root it was written at. Metadata from an
    older writer has no root, so the File nodes are checked instead: an
    absolute path outside the root and outside every declared extra dir can
    only come from a store built elsewhere."""
    from codegraph.state.scan_meta import foreign_root, read_meta

    current = repo_root.resolve()
    meta = read_meta(repo_root) or {}
    if meta.get("root"):
        old = foreign_root(meta, repo_root)
        return f"{old} -> {current}" if old else ""
    if not meta:
        try:
            from codegraph.core.db import get_db_path

            if not get_db_path(repo_root).exists():
                return ""
        except Exception:
            return ""
    try:
        paths = [
            p for (p,) in get_connection(repo_root).list_node_fields("File", ["path"])
        ]
    except Exception:
        return ""
    if not paths:
        return ""
    allowed = {str(repo_root), str(current)}
    try:
        from codegraph.core.config import load_config

        cfg = load_config(repo_root)
        for entry in list(cfg.extra_dirs) + list(cfg.include_dirs):
            p = Path(entry)
            p = p if p.is_absolute() else repo_root / p
            allowed.update({str(p), str(p.resolve())})
    except Exception:
        return ""
    prefixes = tuple(a.rstrip(os.sep) + os.sep for a in allowed)
    for p in paths:
        if os.path.isabs(p) and not p.startswith(prefixes):
            return f"file outside the root: {p}"
    return ""


def index_repo(
    repo_root: str | Path,
    verbose: bool = False,
    on_file: Callable[[Path, str, dict], None] | None = None,
    on_discovery: Callable[[int, str], None] | None = None,
    method: str = "auto",
) -> dict:
    """Index the repo, and if the graph DB is found corrupt mid-index (a
    DuckDB ART index left inconsistent by an earlier crash), rebuild it from
    scratch once and retry a full scan instead of crashing. See _index_repo
    for the discovery-method details.

    Holds the repo's index lock: raises IndexBusy (a RuntimeError) when
    another process or thread is already indexing this repo, rather than
    running a second full index beside it."""
    from codegraph.state.index_lock import index_lock

    with index_lock(repo_root):
        return _index_repo_recovering(
            repo_root,
            verbose=verbose,
            on_file=on_file,
            on_discovery=on_discovery,
            method=method,
        )


def _index_repo_recovering(
    repo_root: str | Path,
    verbose: bool,
    on_file: Callable[[Path, str, dict], None] | None,
    on_discovery: Callable[[int, str], None] | None,
    method: str,
) -> dict:
    try:
        return _index_repo(
            repo_root,
            verbose=verbose,
            on_file=on_file,
            on_discovery=on_discovery,
            method=method,
        )
    except Exception as exc:
        if not _is_graph_corrupt(exc):
            raise
        from codegraph.state.activity import log as _activity_log

        _activity_log(Path(repo_root), "graph_corrupt_recover", str(exc)[:200])
        _recover_corrupt_graph(Path(repo_root))
        # One retry on a fresh graph, forced full so nothing is skipped.
        # Calls _index_repo directly, so a second corruption propagates
        # instead of looping.
        return _index_repo(
            repo_root,
            verbose=verbose,
            on_file=on_file,
            on_discovery=on_discovery,
            method="os_walk",
        )


def _index_repo(
    repo_root: str | Path,
    verbose: bool = False,
    on_file: Callable[[Path, str, dict], None] | None = None,
    on_discovery: Callable[[int, str], None] | None = None,
    method: str = "auto",
) -> dict:
    """
    Walk the repo, index all supported files.

    Discovery strategies (`method`):
      - auto          default; tries git_ls_files, falls back to os_walk
      - git_ls_files  force git; respects .gitignore
      - os_walk       Python os.walk; respects _IGNORE_DIRS + .cghignore
      - find          GNU `find -type f`; fast on huge repos
      - git_diff      only files changed since last scan (uses scan_meta)
      - incremental   only files whose git blob SHA drifted (delegates to
                      incremental_reindex)

    Args:
        repo_root: Repository root.
        verbose: Print each file to stdout (ignored if on_file is set).
        on_file: Callback(file_path, status, stats) called after each file.
                 status is "indexed", "skipped", or "error".
        on_discovery: Callback(total_files, method) called once after
                 file discovery.
        method: One of VALID_METHODS.

    Returns a summary dict.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"method must be one of {VALID_METHODS}, got {method!r}")

    from codegraph.state.activity import log as _activity_log
    from codegraph.state.activity import rotate_if_needed

    repo_root = Path(repo_root)

    # "incremental" is a different workflow (handles deletions, keyed on
    # per-file blob SHAs). Delegate to the dedicated implementation.
    if method == "incremental":
        return incremental_reindex(
            repo_root, on_file=on_file, on_discovery=on_discovery
        )

    method_requested = method

    # A store built at another root (a copied .codegraph, a moved checkout)
    # holds absolute paths of that root. A plain walk would upsert the new
    # paths beside the old ones, leaving every symbol and caller twice. Wipe
    # the graph and index from scratch; the FTS rows of the old paths are
    # dropped by the orphan purge at the end of the scan.
    moved = _foreign_root(repo_root)
    if moved:
        from codegraph.state.scan_meta import clear_meta

        _activity_log(repo_root, "root_moved_rebuild", moved)
        _recover_corrupt_graph(repo_root)
        clear_meta(repo_root)
        if method == "git_diff":
            method = "auto"

    # An index written by an older graph format lacks data that only a parse
    # produces (call sites, for format 2), so every file is parsed again once,
    # mtime cache or not. git_diff would only see changed files: widen it.
    from codegraph.state.scan_meta import graph_format_outdated

    reparse = graph_format_outdated(repo_root)
    if reparse:
        _activity_log(repo_root, "graph_format_upgrade", "full re-parse")
        if method == "git_diff":
            method = "auto"

    stats = {"indexed": 0, "skipped": 0, "errors": 0}
    take_import_coverage()  # drop anything a watcher left behind
    take_import_coverage_partial()
    t0 = time.time()

    rotate_if_needed(repo_root)
    _activity_log(repo_root, f"scan_start:{method}", str(repo_root))

    # Batch-fetch git blob SHAs once, pass to each index_file call
    try:
        from codegraph.state.scan_meta import git_tree_blob_shas

        blob_shas = git_tree_blob_shas(repo_root) or {}
    except Exception:
        blob_shas = {}
    from codegraph.state.scan_meta import git_hash_object as _git_hash

    candidates, deletions, actual_method = _discover_candidates(repo_root, method)

    # Load config once for the whole scan (size cap + ignore patterns), then
    # thread it into every index_file call so config.toml is read a single
    # time per scan instead of once per file.
    from codegraph.core.config import load_config as _load_config

    scan_cfg = _load_config(repo_root)
    parseable = _filter_parseable(candidates, scan_cfg, stats)
    tf_reparse = _module_sources_reparse(repo_root, scan_cfg, reparse, _activity_log)

    if on_discovery:
        on_discovery(len(parseable), actual_method)
    elif verbose:
        print(f"  [codegraph] using {actual_method} ({len(parseable)} parseable files)")

    # Handle deletions first (git_diff only)
    if deletions:
        _delete_gone(repo_root, deletions, _activity_log)

    # Files git does not track at HEAD (untracked, new) are hashed in one git
    # run instead of one subprocess each.
    from codegraph.state.scan_meta import git_hash_objects

    unstaged_shas = git_hash_objects(
        repo_root,
        [p for p in parseable if _rel_or_abs(p, repo_root) not in blob_shas],
    )

    # ------------------------------------------------------------------
    # Index loop (shared across all methods)
    # ------------------------------------------------------------------
    for full_path in parseable:
        try:
            rel = str(full_path.relative_to(repo_root))
        except ValueError:
            rel = str(full_path)
        sha = blob_shas.get(rel) or unstaged_shas.get(str(full_path))
        if sha is None:
            sha = _git_hash(repo_root, full_path)
        ok = index_file(
            full_path,
            repo_root,
            git_blob_sha=sha,
            cfg=scan_cfg,
            reparse=reparse or (tf_reparse and _is_terraform(full_path)),
        )
        status = "indexed" if ok else "error"
        if ok:
            stats["indexed"] += 1
        else:
            stats["errors"] += 1

        if not ok or stats["indexed"] % 25 == 0:
            _activity_log(
                repo_root,
                "scan_progress" if ok else "scan_error",
                f"{stats['indexed']}/{len(parseable)} {rel}",
            )

        if on_file:
            on_file(full_path, status, stats)
        elif verbose:
            print(f"  + {rel}")

    extra_dirs = _index_extra_dirs(repo_root, stats, _activity_log, reparse, tf_reparse)
    repaired = _reparse_unstamped(repo_root, _activity_log)
    if repaired:
        stats["older_writer_reparsed"] = len(repaired)
    purged = _purge_fts_orphans(repo_root, _activity_log)
    if purged:
        stats["fts_orphans_purged"] = purged

    stats["elapsed_s"] = round(time.time() - t0, 2)
    stats["imports"] = take_import_coverage()
    stats["imports_partial"] = take_import_coverage_partial()
    stats["method"] = actual_method
    stats["method_requested"] = method_requested
    stats["extra_dirs"] = extra_dirs
    if deletions:
        stats["deleted"] = len(deletions)

    # Persist scan metadata (git HEAD + branch + stats) for scan_status
    try:
        from codegraph.state.scan_meta import write_meta

        write_meta(repo_root, stats)
    except Exception:
        pass
    _record_module_sources(repo_root, _activity_log)

    _activity_log(
        repo_root,
        "scan_end",
        f"indexed={stats['indexed']} skipped={stats['skipped']} errors={stats['errors']} elapsed={stats['elapsed_s']}s",
    )
    return stats


def incremental_reindex(
    repo_root: str | Path,
    on_file: Callable[[Path, str, dict], None] | None = None,
    on_discovery: Callable[[int, str], None] | None = None,
) -> dict:
    """Reindex only what changed; see _incremental_reindex. Holds the repo's
    index lock like index_repo, and keeps it through a fallback to a full
    index (the lock nests within one thread). Raises IndexBusy when another
    index of this repo is running."""
    from codegraph.state.index_lock import index_lock

    with index_lock(repo_root):
        return _incremental_reindex(
            repo_root, on_file=on_file, on_discovery=on_discovery
        )


def _incremental_reindex(
    repo_root: str | Path,
    on_file: Callable[[Path, str, dict], None] | None = None,
    on_discovery: Callable[[int, str], None] | None = None,
) -> dict:
    """
    Surgical reindex: compare each File node's stored git_blob_sha to the
    current HEAD blob SHA and re-index only files whose blob changed.
    Also re-indexes files present in HEAD but not in the graph (newly added).

    Much faster than scan_repo after a branch switch / pull / rebase.
    Falls back to a full scan if:
      - not a git repo
      - stored File nodes have no git_blob_sha (pre-0.4 DB not yet rescanned)

    Returns a dict with: mode, reindexed, deleted, unchanged, elapsed_s.
    """
    from codegraph.state.activity import log as _activity_log
    from codegraph.state.scan_meta import (
        clear_meta,
        git_tree_blob_shas,
        write_meta,
    )

    repo_root = Path(repo_root)
    t0 = time.time()
    _activity_log(repo_root, "incremental_start", str(repo_root))

    # A moved or foreign index: graph node paths are stored absolute, so if the
    # index was built at a different root (repo zipped from another machine,
    # cloned to a new path) the git blob shas still match and incremental would
    # keep every stale absolute path. Wipe the graph and rebuild from scratch,
    # which recomputes every path from the current root. A plain full walk is
    # not enough: it upserts the new paths but leaves the old-root File nodes
    # orphaned in the graph.
    if _foreign_root(repo_root):
        _activity_log(repo_root, "incremental_fallback", "root moved")
        _recover_corrupt_graph(repo_root)
        clear_meta(repo_root)
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }

    from codegraph.analysis.federation import child_paths_to_skip, is_under_any

    subrepos = child_paths_to_skip(repo_root)

    head_shas = git_tree_blob_shas(repo_root)
    if head_shas is None:
        # Not a git repo, fall back to full scan
        _activity_log(repo_root, "incremental_fallback", "no git")
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }

    # Drop any path that lives under a federated subrepo. The subrepo
    # owns its own index; the parent acts as a passe-plat.
    # An index from an older graph format needs one full re-parse; the blob
    # diff below would leave every unchanged file without the new data.
    from codegraph.state.scan_meta import graph_format_outdated

    if graph_format_outdated(repo_root):
        _activity_log(repo_root, "incremental_fallback", "graph format upgrade")
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }
    # A changed module_sources mapping or pinned ref: the full walk parses
    # the Terraform files again (the others stay skipped by mtime).
    try:
        tf_changed = module_sources_changed(repo_root)
    except Exception:
        tf_changed = False
    if tf_changed:
        _activity_log(repo_root, "incremental_fallback", "module_sources changed")
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }

    if subrepos:
        head_shas = {
            rel: sha
            for rel, sha in head_shas.items()
            if not is_under_any(repo_root / rel, subrepos)
        }

    conn = get_connection(repo_root)

    # Load stored (path, blob_sha) pairs
    stored: dict[str, str | None] = {}
    try:
        for path, sha in conn.list_node_fields("File", ["path", "git_blob_sha"]):
            stored[path] = sha
    except Exception:
        # Old schema / column missing, fall back
        _activity_log(repo_root, "incremental_fallback", "no git_blob_sha column")
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }

    # If nothing is stored OR none of the stored entries have a sha, the
    # index predates per-file blob tracking, do a full scan to populate.
    if not stored or all(sha is None for sha in stored.values()):
        _activity_log(repo_root, "incremental_fallback", "no stored blob shas")
        return {
            "mode": "fallback_full",
            **index_repo(repo_root, on_file=on_file, on_discovery=on_discovery),
        }

    # Diff: paths whose blob_sha changed or that are new
    to_index: list[tuple[str, str]] = []  # (rel_path, blob_sha)
    for rel_path, head_sha in head_shas.items():
        stored_sha = stored.get(str(repo_root / rel_path))
        if stored_sha != head_sha:
            # Only parseable files
            full = repo_root / rel_path
            if is_supported(full) and full.exists():
                to_index.append((rel_path, head_sha))

    # include_dirs: force-reindex by mtime (git-blob diff doesn't see gitignored files)
    include_extras: list[Path] = []
    for p in _walk_include_dirs(repo_root):
        if not (is_supported(p) and p.exists()):
            continue
        try:
            disk_mtime = p.stat().st_mtime
        except OSError:
            continue
        stored_mtime: float | None = None
        try:
            raw = conn.query_node_field("File", "path", str(p), "mtime")
            stored_mtime = float(raw) if raw is not None else None
        except Exception:
            stored_mtime = None
        if stored_mtime is None or abs(stored_mtime - disk_mtime) > 0.01:
            include_extras.append(p)

    # Paths that are gone from HEAD (deleted on this branch), but don't delete
    # include_dir files just because they're absent from git HEAD.
    include_roots = [str(r) for r in resolve_include_dirs_safe(repo_root)]
    head_abs = {str(repo_root / p) for p in head_shas}

    def _under_include(path_str: str) -> bool:
        return any(path_str.startswith(root + os.sep) for root in include_roots)

    def _under_subrepo(path_str: str) -> bool:
        if not subrepos:
            return False
        return is_under_any(path_str, subrepos)

    to_delete = [
        p
        for p in stored
        if p not in head_abs and not _under_include(p) and not _under_subrepo(p)
    ]

    fts_conn = _get_fts(repo_root) if repo_root else None

    # Delete stale File nodes + attached graph nodes. A failed delete
    # leaves ghost nodes and stale FTS rows behind, so it is logged
    # (same as the full-scan path), never swallowed.
    from codegraph.state.activity import log as _act_log

    deleted_count = 0
    delete_errors = 0
    for path in to_delete:
        try:
            _delete_file_relinking(conn, path, repo_root)
            if fts_conn is not None:
                delete_file_symbols(fts_conn, path)
            from codegraph.state.findings import purge_file_findings

            purge_file_findings(repo_root, path)
            deleted_count += 1
        except Exception as exc:
            delete_errors += 1
            _act_log(repo_root, "scan_error", f"delete failed for {path}: {exc}")

    # Re-index changed/new files. Drive the progress callbacks (a spinner /
    # bar in the CLI) the same way the full scan does, so an incremental run
    # is not a silent wait.
    if on_discovery is not None:
        on_discovery(len(to_index) + len(include_extras), "incremental")

    reindexed: list[str] = []
    errors = 0

    def _progress(full: Path, ok: bool) -> None:
        if on_file is not None:
            on_file(full, "indexed" if ok else "error", {"errors": errors})

    for rel_path, blob_sha in to_index:
        full = repo_root / rel_path
        ok = False
        try:
            ok = bool(index_file(full, repo_root, force=True, git_blob_sha=blob_sha))
            if ok:
                reindexed.append(rel_path)
            else:
                errors += 1
        except Exception:
            errors += 1
        _progress(full, ok)

    # Re-index include_dir files flagged by mtime (gitignored but force-included)
    for full in include_extras:
        ok = False
        try:
            ok = bool(index_file(full, repo_root, force=True))
            if ok:
                try:
                    reindexed.append(str(full.relative_to(repo_root)))
                except ValueError:
                    reindexed.append(str(full))
            else:
                errors += 1
        except Exception:
            errors += 1
        _progress(full, ok)

    # Files an older cgh rewrote (a watcher save, a single-file index) keep
    # the blob sha it stored, so the diff above sees nothing to do for them.
    for path in _reparse_unstamped(repo_root, _act_log):
        try:
            reindexed.append(str(Path(path).relative_to(repo_root)))
        except ValueError:
            reindexed.append(path)

    purged = _purge_fts_orphans(repo_root, _act_log)

    elapsed = round(time.time() - t0, 2)
    result = {
        "mode": "incremental",
        "reindexed": reindexed,
        "reindexed_count": len(reindexed),
        "deleted": to_delete,
        "deleted_count": deleted_count,
        "fts_orphans_purged": purged,
        "unchanged_count": max(0, len(head_shas) - len(to_index)),
        "errors": errors,
        "elapsed_s": elapsed,
    }

    # Refresh scan metadata (HEAD + branch) since we just caught up
    try:
        write_meta(
            repo_root,
            {
                "indexed": len(reindexed),
                "skipped": result["unchanged_count"],
                "errors": errors,
                "elapsed_s": elapsed,
                "method": "incremental",
            },
        )
    except Exception:
        pass
    _record_module_sources(repo_root, _activity_log)

    _activity_log(
        repo_root,
        "incremental_end",
        f"reindexed={len(reindexed)} deleted={deleted_count} elapsed={elapsed}s",
    )
    return result
