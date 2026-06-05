# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Orchestrates parsing + Kuzu ingestion.
#              Supports full index (scan all files) and incremental update
#              (re-index a single changed file, purge stale nodes first).

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from codegraph.core.db import get_connection
from codegraph.core.fts import commit as fts_commit
from codegraph.core.fts import delete_file_symbols, get_fts_conn, upsert_symbol
from .parsers import get_parser, is_supported
from .parsers.base import FileIndex

# Default Python recursion limit is 1000. Tree-sitter walks on deeply nested
# code (long method chains, big JSX trees, generated protobufs) can blow past
# that. Raise once at import time so we don't pay the cost on every call.
# Ported from graphify, same constant.
_RECURSION_LIMIT = 10_000
if sys.getrecursionlimit() < _RECURSION_LIMIT:
    sys.setrecursionlimit(_RECURSION_LIMIT)

_fts_conn = None


def _get_fts(repo_root):
    global _fts_conn
    if _fts_conn is None:
        _fts_conn = get_fts_conn(repo_root)
    return _fts_conn


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

_CGHIGNORE_FILE = ".cghignore"
_cghignore_patterns: list[str] | None = None


def _load_cghignore(repo_root: Path) -> list[str]:
    """Load .cghignore patterns (gitignore syntax). Cached after first load."""
    global _cghignore_patterns
    if _cghignore_patterns is not None:
        return _cghignore_patterns

    ignore_file = repo_root / _CGHIGNORE_FILE
    if not ignore_file.exists():
        _cghignore_patterns = []
        return _cghignore_patterns

    patterns = []
    for line in ignore_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip negation patterns (!), we don't support them in our simple matcher
        if line.startswith("!"):
            continue
        patterns.append(line)

    _cghignore_patterns = patterns
    return _cghignore_patterns


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
        if "/" not in pattern and any(fnmatch.fnmatch(part, pattern) for part in Path(rel).parts):
            return True

    return False


# ---------------------------------------------------------------------------
# Kuzu upsert helpers
# ---------------------------------------------------------------------------


def _upsert_file(
    conn,
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


def _purge_file(conn, path: str, fts_conn=None) -> None:
    """Delete all nodes + edges associated with a file before re-indexing it.

    Delegates the graph cleanup to the backend's purge_file_data helper,
    each backend knows its own table layout and constraint model.
    """
    conn.purge_file_data(path)
    if fts_conn is not None:
        delete_file_symbols(fts_conn, path)


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
        kind = f"tf_{res.kind}" if res.kind in ("variable", "output") else f"tf_{res.kind}"
        upsert_symbol(
            fts_conn,
            sym_id=res.id,
            kind=kind,
            name=res.name,
            file_path=res.file_path,
            start_line=res.start_line,
            end_line=res.end_line,
            docstring=res.type,
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


def _resolve_calls(conn, functions: list, lang: str = "") -> None:
    """
    After all Function nodes exist, create CALLS edges by matching call
    names to known function names. Best-effort: unresolved names are skipped.

    Names matching a language built-in callable are filtered first (see
    parsers/builtins.py) so callees like isinstance / println / parseInt
    don't accumulate spurious edges. The actual edge-link work goes
    through find_node_keys + ensure_edge so it stays backend-neutral.
    """
    from codegraph.parsers.builtins import is_builtin

    for fn in functions:
        for called_name in fn.calls:
            if lang and is_builtin(lang, called_name):
                continue
            for callee_id in conn.find_node_keys("Function", "name", called_name):
                conn.ensure_edge("CALLS", fn.id, callee_id)


def _resolve_inherits(conn, classes: list) -> None:
    """Create INHERITS edges between Class nodes using base class names."""
    for cls in classes:
        for base_name in cls.bases:
            for parent_id in conn.find_node_keys("Class", "name", base_name):
                conn.ensure_edge("INHERITS", cls.id, parent_id)


def _ingest_code(conn, idx: FileIndex) -> None:
    """Ingest functions, classes, and their edges (Python, TypeScript, Vue, etc.)."""
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

    _resolve_calls(conn, idx.functions, idx.lang)
    _resolve_inherits(conn, idx.classes)


def _ingest_imports(conn, idx: FileIndex, repo_root: Path | None) -> None:
    """
    Wire IMPORTS edges from idx.imports into Kuzu.

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
    for imp in idx.imports:
        target = resolve_import(idx.lang, imp.source_module, idx.path, repo_root)
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

        # Symbol annotation on the edge. If the import named multiple
        # symbols, write one edge per symbol so MCP tools can answer
        # "who imports name X". Single edge with empty symbol when the
        # import is a whole-module pull.
        symbols = imp.symbols if imp.symbols else [""]
        for sym in symbols:
            edge_key = f"{target_str}::{sym}"
            if edge_key in seen_targets:
                continue
            seen_targets.add(edge_key)
            conn.ensure_edge("IMPORTS", idx.path, target_str, {"symbol": sym})


def _ingest_terraform(conn, idx: FileIndex) -> None:
    """Ingest terraform resources and variables from unified FileIndex."""
    for res in idx.resources:
        if res.kind in ("variable", "output"):
            conn.upsert_node(
                "TFVar",
                "id",
                res.id,
                {
                    "name": res.name,
                    "kind": res.kind,
                    "file_path": res.file_path,
                    "start_line": res.start_line,
                },
            )
            conn.ensure_edge("DEFINES_TFVAR", res.file_path, res.id)
        else:
            conn.upsert_node(
                "TFResource",
                "id",
                res.id,
                {
                    "name": res.name,
                    "type": res.type,
                    "file_path": res.file_path,
                    "start_line": res.start_line,
                    "end_line": res.end_line,
                },
            )
            conn.ensure_edge("DEFINES_RESOURCE", res.file_path, res.id)


def _ingest_endpoints(conn, path: Path) -> int:
    """Extract and persist HTTP endpoints from a file. Returns count."""
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
            # works on both Kuzu and DuckDB.
            for fn_id in conn.find_node_keys("Function", "name", ep.handler_name):
                # find_node_keys returns *all* matches; filter to this file
                # by checking the id prefix (id = file_path + '::' + name).
                if fn_id.startswith(f"{path}::") or f"::{path}::" in fn_id:
                    conn.ensure_edge("IMPLEMENTED_BY", ep.id, fn_id)
    return len(eps)


def _ingest_markdown(conn, idx: FileIndex) -> None:
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
    # The original Cypher used `WHERE f.path ENDS WITH $tp` which has no
    # direct find_node_keys equivalent; for now resolve via find_node_keys
    # over all files and filter in Python, small N (file count) makes
    # this cheap.
    for link in idx.links:
        target = link.target
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path = target.split("#")[0]
        if not target_path:
            continue
        section = _find_section_for_line(idx.sections, link.line)
        if not section:
            continue
        for file_key in conn.find_node_keys("File", "path", target_path):
            # Exact match path. We can extend with ENDS WITH later when a
            # backend-neutral suffix-match helper exists.
            conn.ensure_edge(
                "MD_LINKS_TO", section.id, file_key, {"label": link.label}
            )

    # Code references: link sections to code symbols they mention
    for ref in idx.code_refs:
        section = _find_section_for_line(idx.sections, ref.line)
        if not section:
            continue
        for fn_id in conn.find_node_keys("Function", "name", ref.symbol):
            conn.ensure_edge(
                "MD_REFS_SYMBOL", section.id, fn_id, {"context": ref.context}
            )
        for cls_id in conn.find_node_keys("Class", "name", ref.symbol):
            conn.ensure_edge(
                "MD_REFS_CLASS", section.id, cls_id, {"context": ref.context}
            )


def _find_section_for_line(sections: list, line: int):
    """Find the deepest (most specific) section containing a given line."""
    best = None
    for sec in sections:
        if sec.start_line <= line <= sec.end_line:
            if best is None or sec.level > best.level:
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
) -> bool:
    """
    Parse and ingest a single file into the graph.
    Returns True on success, False if the file type is unsupported or parse fails.

    Args:
        path: File to index.
        repo_root: Repository root (default: CWD).
        force: If True, index even if the file is in .gitignore or .git/info/exclude.
               Skips mtime cache check too, always re-parses.
    """
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

    conn = get_connection(repo_root)
    mtime = path.stat().st_mtime

    # Check if already indexed and unchanged (skip if force)
    if not force:
        try:
            stored_mtime = conn.query_node_field("File", "path", str(path), "mtime")
            if stored_mtime is not None and abs(float(stored_mtime) - mtime) < 0.01:
                return True  # unchanged
        except Exception:
            pass

    fts_conn = _get_fts(repo_root) if repo_root else None
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
        return False
    except Exception as exc:
        # Catch-all: any other parse failure (decoding error, malformed source,
        # tree-sitter binding bug, ...) skips this one file instead of taking
        # down the whole scan.
        from codegraph.state.activity import log as _act_log

        msg = f"{path}: {type(exc).__name__}: {exc}"
        print(f"[codegraph] parse error: {msg}", file=sys.stderr, flush=True)
        _act_log(root, "parse_error", msg)
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

    # Ingest into graph
    if idx.functions or idx.classes:
        _ingest_code(conn, idx)
    if idx.resources:
        _ingest_terraform(conn, idx)
    if idx.sections:
        _ingest_markdown(conn, idx)

    # IMPORTS edges, wire them up after the File node exists, regardless
    # of whether the file defines functions/classes (pure __init__.py
    # re-export modules still have meaningful imports).
    if idx.imports:
        _ingest_imports(conn, idx, root)

    # HTTP endpoints (after functions are in place so IMPLEMENTED_BY can link)
    _ingest_endpoints(conn, path)

    # Ingest into FTS
    _fts_ingest(fts_conn, idx)

    return True


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
            text=True, encoding="utf-8", errors="replace",
            cwd=str(repo_root),
            timeout=30,
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
    from codegraph.core.config import resolve_include_dirs
    from codegraph.analysis.federation import child_paths_to_skip, is_under_any

    seen = seen or set()
    out: list[Path] = []
    try:
        include_dirs = resolve_include_dirs(repo_root)
    except Exception:
        return out
    subrepos = child_paths_to_skip(repo_root)
    for base in include_dirs:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
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

    try:
        r = subprocess.run(
            ["find", str(repo_root), "-type", "f", "-not", "-path", "*/.*"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=60,
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
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        # Prune subrepo directories so we don't even descend into them
        if subrepos:
            dirnames[:] = [d for d in dirnames if not is_under_any(Path(dirpath) / d, subrepos)]
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
            text=True, encoding="utf-8", errors="replace",
            cwd=str(repo_root),
            timeout=10,
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


def index_repo(
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
        return incremental_reindex(repo_root)

    stats = {"indexed": 0, "skipped": 0, "errors": 0}
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

    # ------------------------------------------------------------------
    # File discovery, each method returns (files_to_index, actual_method)
    # ------------------------------------------------------------------
    actual_method = method
    candidates: list[Path] = []
    deletions: list[Path] = []

    if method in ("auto", "git_ls_files"):
        git_files = _git_tracked_files(repo_root)
        if git_files is None:
            if method == "git_ls_files":
                raise RuntimeError("git_ls_files requested but git is unavailable or repo not initialised")
            # auto → fall back
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
            # No prior scan meta → do a full scan instead
            actual_method = "git_ls_files"
            git_files = _git_tracked_files(repo_root)
            candidates = list(git_files) if git_files is not None else _discover_os_walk(repo_root)
            if git_files is None:
                actual_method = "os_walk"

    # Filter to parseable, existing files
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
        parseable.append(p)

    if on_discovery:
        on_discovery(len(parseable), actual_method)
    elif verbose:
        print(f"  [codegraph] using {actual_method} ({len(parseable)} parseable files)")

    # Handle deletions first (git_diff only)
    if deletions:
        fts_conn = _get_fts(repo_root)
        conn = get_connection(repo_root)
        for gone in deletions:
            try:
                conn.delete_file_completely(str(gone))
                if fts_conn is not None:
                    delete_file_symbols(fts_conn, str(gone))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Index loop (shared across all methods)
    # ------------------------------------------------------------------
    for full_path in parseable:
        try:
            rel = str(full_path.relative_to(repo_root))
        except ValueError:
            rel = str(full_path)
        sha = blob_shas.get(rel)
        if sha is None:
            sha = _git_hash(repo_root, full_path)
        ok = index_file(full_path, repo_root, git_blob_sha=sha)
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

    # Also index extra_dirs from config.toml
    extra_dirs: list[str] = []
    try:
        import tomllib

        cfg = repo_root / ".codegraph" / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                cfg_data = tomllib.load(f)
            extra_dirs = cfg_data.get("codegraph", {}).get("extra_dirs", [])
    except Exception:
        pass

    for rel in extra_dirs:
        extra_root = (repo_root / rel).resolve()
        if not extra_root.exists() or not extra_root.is_dir():
            continue
        _activity_log(repo_root, "extra_dir_scan", str(extra_root))
        for dirpath, dirnames, filenames in os.walk(extra_root):
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
            for filename in filenames:
                full_path = Path(dirpath) / filename
                if not is_supported(full_path):
                    continue
                try:
                    ok = index_file(full_path, repo_root)
                    if ok:
                        stats["indexed"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception:
                    stats["errors"] += 1

    stats["elapsed_s"] = round(time.time() - t0, 2)
    stats["method"] = actual_method
    stats["method_requested"] = method
    stats["extra_dirs"] = extra_dirs
    if deletions:
        stats["deleted"] = len(deletions)

    # Persist scan metadata (git HEAD + branch + stats) for scan_status
    try:
        from codegraph.state.scan_meta import write_meta

        write_meta(repo_root, stats)
    except Exception:
        pass

    _activity_log(
        repo_root,
        "scan_end",
        f"indexed={stats['indexed']} skipped={stats['skipped']} errors={stats['errors']} elapsed={stats['elapsed_s']}s",
    )
    return stats


def incremental_reindex(repo_root: str | Path) -> dict:
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
    from codegraph.state.scan_meta import git_tree_blob_shas, write_meta

    repo_root = Path(repo_root)
    t0 = time.time()
    _activity_log(repo_root, "incremental_start", str(repo_root))

    from codegraph.analysis.federation import child_paths_to_skip, is_under_any

    subrepos = child_paths_to_skip(repo_root)

    head_shas = git_tree_blob_shas(repo_root)
    if head_shas is None:
        # Not a git repo, fall back to full scan
        _activity_log(repo_root, "incremental_fallback", "no git")
        return {"mode": "fallback_full", **index_repo(repo_root)}

    # Drop any path that lives under a federated subrepo. The subrepo
    # owns its own index; the parent acts as a passe-plat.
    if subrepos:
        head_shas = {rel: sha for rel, sha in head_shas.items() if not is_under_any(repo_root / rel, subrepos)}

    conn = get_connection(repo_root)

    # Load stored (path, blob_sha) pairs
    stored: dict[str, str | None] = {}
    try:
        for path, sha in conn.list_node_fields("File", ["path", "git_blob_sha"]):
            stored[path] = sha
    except Exception:
        # Old schema / column missing, fall back
        _activity_log(repo_root, "incremental_fallback", "no git_blob_sha column")
        return {"mode": "fallback_full", **index_repo(repo_root)}

    # If nothing is stored OR none of the stored entries have a sha, the
    # index predates per-file blob tracking, do a full scan to populate.
    if not stored or all(sha is None for sha in stored.values()):
        _activity_log(repo_root, "incremental_fallback", "no stored blob shas")
        return {"mode": "fallback_full", **index_repo(repo_root)}

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

    to_delete = [p for p in stored if p not in head_abs and not _under_include(p) and not _under_subrepo(p)]

    fts_conn = _get_fts(repo_root) if repo_root else None

    # Delete stale File nodes + attached graph nodes
    deleted_count = 0
    for path in to_delete:
        try:
            conn.delete_file_completely(path)
            if fts_conn is not None:
                delete_file_symbols(fts_conn, path)
            deleted_count += 1
        except Exception:
            pass

    # Re-index changed/new files
    reindexed: list[str] = []
    errors = 0
    for rel_path, blob_sha in to_index:
        full = repo_root / rel_path
        try:
            if index_file(full, repo_root, force=True, git_blob_sha=blob_sha):
                reindexed.append(rel_path)
            else:
                errors += 1
        except Exception:
            errors += 1

    # Re-index include_dir files flagged by mtime (gitignored but force-included)
    for full in include_extras:
        try:
            if index_file(full, repo_root, force=True):
                try:
                    reindexed.append(str(full.relative_to(repo_root)))
                except ValueError:
                    reindexed.append(str(full))
            else:
                errors += 1
        except Exception:
            errors += 1

    elapsed = round(time.time() - t0, 2)
    result = {
        "mode": "incremental",
        "reindexed": reindexed,
        "reindexed_count": len(reindexed),
        "deleted": to_delete,
        "deleted_count": deleted_count,
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

    _activity_log(
        repo_root,
        "incremental_end",
        f"reindexed={len(reindexed)} deleted={deleted_count} elapsed={elapsed}s",
    )
    return result
