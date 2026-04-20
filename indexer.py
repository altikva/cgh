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
import time
from collections.abc import Callable
from pathlib import Path

import kuzu

from .db import get_connection
from .fts import commit as fts_commit
from .fts import delete_file_symbols, get_fts_conn, upsert_symbol
from .parsers import get_parser, is_supported
from .parsers.base import FileIndex

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
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip negation patterns (!) — we don't support them in our simple matcher
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

    try:
        rel = str(file_path.relative_to(repo_root))
    except ValueError:
        rel = str(file_path)

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
    conn: kuzu.Connection,
    path: str,
    lang: str,
    mtime: float,
    git_blob_sha: str | None = None,
    role: str | None = None,
    layer: str | None = None,
    module_doc: str | None = None,
) -> None:
    conn.execute(
        "MERGE (f:File {path: $p}) SET "
        "f.lang = $l, f.mtime = $m, f.git_blob_sha = $g, "
        "f.role = $r, f.layer = $ly, f.module_doc = $d",
        {
            "p": path,
            "l": lang,
            "m": mtime,
            "g": git_blob_sha,
            "r": role,
            "ly": layer,
            "d": module_doc,
        },
    )


def _purge_file(conn: kuzu.Connection, path: str, fts_conn=None) -> None:
    """Delete all nodes + edges associated with a file before re-indexing it."""
    conn.execute(
        "MATCH (fn:Function) WHERE fn.file_path = $p DETACH DELETE fn",
        {"p": path},
    )
    conn.execute(
        "MATCH (c:Class) WHERE c.file_path = $p DETACH DELETE c",
        {"p": path},
    )
    conn.execute(
        "MATCH (r:TFResource) WHERE r.file_path = $p DETACH DELETE r",
        {"p": path},
    )
    conn.execute(
        "MATCH (v:TFVar) WHERE v.file_path = $p DETACH DELETE v",
        {"p": path},
    )
    conn.execute(
        "MATCH (s:MdSection) WHERE s.file_path = $p DETACH DELETE s",
        {"p": path},
    )
    conn.execute(
        "MATCH (e:Endpoint) WHERE e.file_path = $p DETACH DELETE e",
        {"p": path},
    )
    conn.execute(
        "MATCH (f:File {path: $p})-[r:IMPORTS]->() DELETE r",
        {"p": path},
    )
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


def _resolve_calls(conn: kuzu.Connection, functions: list) -> None:
    """
    After all Function nodes exist, create CALLS edges by matching call
    names to known function names.  Best-effort: unresolved names are skipped.
    """
    for fn in functions:
        for called_name in fn.calls:
            # Find any function with this name (prefer same file, fall back to any)
            conn.execute(
                """MATCH (caller:Function {id:$cid}), (callee:Function)
                   WHERE callee.name = $n
                   MERGE (caller)-[:CALLS]->(callee)""",
                {"cid": fn.id, "n": called_name},
            )


def _resolve_inherits(conn: kuzu.Connection, classes: list) -> None:
    """Create INHERITS edges between Class nodes using base class names."""
    for cls in classes:
        for base_name in cls.bases:
            conn.execute(
                """MATCH (child:Class {id:$cid}), (parent:Class)
                   WHERE parent.name = $n
                   MERGE (child)-[:INHERITS]->(parent)""",
                {"cid": cls.id, "n": base_name},
            )


def _ingest_code(conn: kuzu.Connection, idx: FileIndex) -> None:
    """Ingest functions, classes, and their edges (Python, TypeScript, Vue, etc.)."""
    for fn in idx.functions:
        conn.execute(
            """MERGE (f:Function {id: $id})
               SET f.name=$n, f.file_path=$fp,
                   f.start_line=$sl, f.end_line=$el, f.docstring=$doc""",
            {
                "id": fn.id,
                "n": fn.name,
                "fp": fn.file_path,
                "sl": fn.start_line,
                "el": fn.end_line,
                "doc": fn.docstring,
            },
        )
        conn.execute(
            """MATCH (f:File {path:$fp}), (fn:Function {id:$id})
               MERGE (f)-[:DEFINES_FN]->(fn)""",
            {"fp": fn.file_path, "id": fn.id},
        )

    for cls in idx.classes:
        conn.execute(
            """MERGE (c:Class {id:$id})
               SET c.name=$n, c.file_path=$fp,
                   c.start_line=$sl, c.end_line=$el, c.docstring=$doc""",
            {
                "id": cls.id,
                "n": cls.name,
                "fp": cls.file_path,
                "sl": cls.start_line,
                "el": cls.end_line,
                "doc": cls.docstring,
            },
        )
        conn.execute(
            """MATCH (f:File {path:$fp}), (c:Class {id:$id})
               MERGE (f)-[:DEFINES_CLASS]->(c)""",
            {"fp": cls.file_path, "id": cls.id},
        )

    for fn in idx.functions:
        if fn.class_name:
            class_id = f"{fn.file_path}::{fn.class_name}"
            conn.execute(
                """MATCH (c:Class {id:$cid}), (fn:Function {id:$fid})
                   MERGE (c)-[:HAS_METHOD]->(fn)""",
                {"cid": class_id, "fid": fn.id},
            )

    _resolve_calls(conn, idx.functions)
    _resolve_inherits(conn, idx.classes)


def _ingest_terraform(conn: kuzu.Connection, idx: FileIndex) -> None:
    """Ingest terraform resources and variables from unified FileIndex."""
    for res in idx.resources:
        if res.kind in ("variable", "output"):
            conn.execute(
                """MERGE (v:TFVar {id:$id})
                   SET v.name=$n, v.kind=$k, v.file_path=$fp, v.start_line=$sl""",
                {"id": res.id, "n": res.name, "k": res.kind, "fp": res.file_path, "sl": res.start_line},
            )
            conn.execute(
                """MATCH (f:File {path:$fp}), (v:TFVar {id:$id})
                   MERGE (f)-[:DEFINES_TFVAR]->(v)""",
                {"fp": res.file_path, "id": res.id},
            )
        else:
            conn.execute(
                """MERGE (r:TFResource {id:$id})
                   SET r.name=$n, r.type=$t, r.file_path=$fp,
                       r.start_line=$sl, r.end_line=$el""",
                {
                    "id": res.id,
                    "n": res.name,
                    "t": res.type,
                    "fp": res.file_path,
                    "sl": res.start_line,
                    "el": res.end_line,
                },
            )
            conn.execute(
                """MATCH (f:File {path:$fp}), (r:TFResource {id:$id})
                   MERGE (f)-[:DEFINES_RESOURCE]->(r)""",
                {"fp": res.file_path, "id": res.id},
            )


def _ingest_endpoints(conn: kuzu.Connection, path: Path) -> int:
    """Extract and persist HTTP endpoints from a file. Returns count."""
    from .endpoints import extract as _extract_endpoints

    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    eps = _extract_endpoints(path, src)
    if not eps:
        return 0

    # Purge old endpoints for this file first
    conn.execute(
        "MATCH (e:Endpoint) WHERE e.file_path = $p DETACH DELETE e",
        {"p": str(path)},
    )

    for ep in eps:
        conn.execute(
            """MERGE (e:Endpoint {id: $id}) SET
                 e.method     = $m,
                 e.path       = $path,
                 e.framework  = $f,
                 e.file_path  = $fp,
                 e.start_line = $sl""",
            {
                "id": ep.id,
                "m": ep.method,
                "path": ep.path,
                "f": ep.framework,
                "fp": ep.file_path,
                "sl": ep.start_line,
            },
        )
        conn.execute(
            """MATCH (f:File {path: $fp}), (e:Endpoint {id: $id})
               MERGE (f)-[:DEFINES_ENDPOINT]->(e)""",
            {"fp": str(path), "id": ep.id},
        )
        if ep.handler_name:
            # Link to the handler Function. We don't know its class so try
            # by name only — safe, best-effort.
            conn.execute(
                """MATCH (e:Endpoint {id: $id}), (fn:Function)
                   WHERE fn.name = $n AND fn.file_path = $fp
                   MERGE (e)-[:IMPLEMENTED_BY]->(fn)""",
                {"id": ep.id, "n": ep.handler_name, "fp": str(path)},
            )
    return len(eps)


def _ingest_markdown(conn: kuzu.Connection, idx: FileIndex) -> None:
    # Sections
    for sec in idx.sections:
        conn.execute(
            """MERGE (s:MdSection {id: $id})
               SET s.title=$t, s.level=$lv, s.file_path=$fp,
                   s.start_line=$sl, s.end_line=$el,
                   s.body_preview=$bp, s.anchor=$a""",
            {
                "id": sec.id,
                "t": sec.title,
                "lv": sec.level,
                "fp": sec.file_path,
                "sl": sec.start_line,
                "el": sec.end_line,
                "bp": sec.body_preview,
                "a": sec.anchor,
            },
        )
        conn.execute(
            """MATCH (f:File {path:$fp}), (s:MdSection {id:$id})
               MERGE (f)-[:DEFINES_SECTION]->(s)""",
            {"fp": sec.file_path, "id": sec.id},
        )

    # Section hierarchy: parent contains child when child.level > parent.level
    # and child comes before the next same-or-higher-level section
    for i, parent in enumerate(idx.sections):
        for j in range(i + 1, len(idx.sections)):
            child = idx.sections[j]
            if child.level <= parent.level:
                break
            if child.level == parent.level + 1:
                conn.execute(
                    """MATCH (p:MdSection {id:$pid}), (c:MdSection {id:$cid})
                       MERGE (p)-[:CONTAINS_SECTION]->(c)""",
                    {"pid": parent.id, "cid": child.id},
                )

    # Internal links: link markdown sections to files they reference
    for link in idx.links:
        target = link.target
        # Skip external URLs and anchors
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Strip anchor from path
        target_path = target.split("#")[0]
        if not target_path:
            continue
        # Find which section this link belongs to
        section = _find_section_for_line(idx.sections, link.line)
        if section:
            conn.execute(
                """MATCH (s:MdSection {id:$sid}), (f:File)
                   WHERE f.path ENDS WITH $tp
                   MERGE (s)-[:MD_LINKS_TO {label: $lb}]->(f)""",
                {"sid": section.id, "tp": target_path, "lb": link.label},
            )

    # Code references: link sections to code symbols they mention
    for ref in idx.code_refs:
        section = _find_section_for_line(idx.sections, ref.line)
        if not section:
            continue
        # Try to match against Function nodes
        conn.execute(
            """MATCH (s:MdSection {id:$sid}), (fn:Function)
               WHERE fn.name = $sym
               MERGE (s)-[:MD_REFS_SYMBOL {context: $ctx}]->(fn)""",
            {"sid": section.id, "sym": ref.symbol, "ctx": ref.context},
        )
        # Try to match against Class nodes
        conn.execute(
            """MATCH (s:MdSection {id:$sid}), (c:Class)
               WHERE c.name = $sym
               MERGE (s)-[:MD_REFS_CLASS {context: $ctx}]->(c)""",
            {"sid": section.id, "sym": ref.symbol, "ctx": ref.context},
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
               Skips mtime cache check too — always re-parses.
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
        res = conn.execute("MATCH (f:File {path:$p}) RETURN f.mtime", {"p": str(path)})
        try:
            while res.has_next():
                row = res.get_next()
                stored_mtime = float(row[0])
                if abs(stored_mtime - mtime) < 0.01:
                    return True  # unchanged
                break
        except Exception:
            pass

    fts_conn = _get_fts(repo_root) if repo_root else None
    _purge_file(conn, str(path), fts_conn)

    try:
        idx = parser.parse(path)
    except Exception as exc:
        print(f"[codegraph] parse error {path}: {exc}")
        return False

    lang = idx.lang

    # Compute git blob SHA for surgical-reindex detection. Caller can pass
    # it in (batched lookup) or we fall back to a per-file subprocess.
    blob_sha = git_blob_sha
    if blob_sha is None:
        try:
            from .scan_meta import git_hash_object

            blob_sha = git_hash_object(root, path)
        except Exception:
            pass

    # Role + layer classification + module-level summary for arch tools
    from .module_doc import extract as _extract_doc
    from .roles import classify as _classify_role

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

    # HTTP endpoints (after functions are in place so IMPLEMENTED_BY can link)
    _ingest_endpoints(conn, path)

    # Ingest into FTS
    _fts_ingest(fts_conn, idx)

    return True


def _git_tracked_files(repo_root: Path) -> list[Path] | None:
    """
    Use `git ls-files` to get tracked + untracked-not-ignored files.
    Also filters out _IGNORE_DIRS (node_modules, .venv, etc.) as extra safety.
    Returns None if not a git repo or git command fails (fallback to os.walk).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
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
            files.append(full)
        # Merge in include_dirs (force-index even when gitignored)
        files.extend(_walk_include_dirs(repo_root, seen=set(files)))
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def resolve_include_dirs_safe(repo_root: Path) -> list[Path]:
    try:
        from .config import resolve_include_dirs

        return resolve_include_dirs(repo_root)
    except Exception:
        return []


def _walk_include_dirs(repo_root: Path, seen: set[Path] | None = None) -> list[Path]:
    """Walk configured include_dirs (config.toml) and return files not yet seen."""
    from .config import resolve_include_dirs

    seen = seen or set()
    out: list[Path] = []
    try:
        include_dirs = resolve_include_dirs(repo_root)
    except Exception:
        return out
    for base in include_dirs:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
            for filename in filenames:
                p = Path(dirpath) / filename
                if p in seen:
                    continue
                if _is_cghignored(p, repo_root):
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
            text=True,
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
    """Python os.walk — portable, respects _IGNORE_DIRS + .cghignore."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        for filename in filenames:
            p = Path(dirpath) / filename
            if _is_cghignored(p, repo_root):
                continue
            out.append(p)
    return out


def _discover_git_diff(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """
    Return (changed_files, deleted_files) since the last scan (from scan_meta).
    Falls back to an empty list when no prior scan exists — caller should
    switch to a full method in that case.
    """
    import subprocess

    from .scan_meta import read_meta

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

    from .activity import log as _activity_log
    from .activity import rotate_if_needed

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
        from .scan_meta import git_tree_blob_shas

        blob_shas = git_tree_blob_shas(repo_root) or {}
    except Exception:
        blob_shas = {}
    from .scan_meta import git_hash_object as _git_hash

    # ------------------------------------------------------------------
    # File discovery — each method returns (files_to_index, actual_method)
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
            # Tool missing or errored — fall back
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
                _purge_file(conn, str(gone), fts_conn)
                conn.execute("MATCH (f:File {path: $p}) DETACH DELETE f", {"p": str(gone)})
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
        from .scan_meta import write_meta

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
    from .activity import log as _activity_log
    from .scan_meta import git_tree_blob_shas, write_meta

    repo_root = Path(repo_root)
    t0 = time.time()
    _activity_log(repo_root, "incremental_start", str(repo_root))

    head_shas = git_tree_blob_shas(repo_root)
    if head_shas is None:
        # Not a git repo — fall back to full scan
        _activity_log(repo_root, "incremental_fallback", "no git")
        return {"mode": "fallback_full", **index_repo(repo_root)}

    conn = get_connection(repo_root)

    # Load stored (path, blob_sha) pairs
    stored: dict[str, str | None] = {}
    try:
        res = conn.execute("MATCH (f:File) RETURN f.path, f.git_blob_sha")
        while res.has_next():
            row = res.get_next()
            stored[row[0]] = row[1]
    except Exception:
        # Old schema / column missing — fall back
        _activity_log(repo_root, "incremental_fallback", "no git_blob_sha column")
        return {"mode": "fallback_full", **index_repo(repo_root)}

    # If nothing is stored OR none of the stored entries have a sha, the
    # index predates per-file blob tracking — do a full scan to populate.
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
            res = conn.execute("MATCH (f:File {path: $p}) RETURN f.mtime", {"p": str(p)})
            if res.has_next():
                row = res.get_next()
                if row[0] is not None:
                    stored_mtime = float(row[0])
        except Exception:
            stored_mtime = None
        if stored_mtime is None or abs(stored_mtime - disk_mtime) > 0.01:
            include_extras.append(p)

    # Paths that are gone from HEAD (deleted on this branch) — but don't delete
    # include_dir files just because they're absent from git HEAD.
    include_roots = [str(r) for r in resolve_include_dirs_safe(repo_root)]
    head_abs = {str(repo_root / p) for p in head_shas}

    def _under_include(path_str: str) -> bool:
        return any(path_str.startswith(root + os.sep) for root in include_roots)

    to_delete = [p for p in stored if p not in head_abs and not _under_include(p)]

    fts_conn = _get_fts(repo_root) if repo_root else None

    # Delete stale File nodes + attached graph nodes
    deleted_count = 0
    for path in to_delete:
        try:
            _purge_file(conn, path, fts_conn)
            conn.execute("MATCH (f:File {path: $p}) DETACH DELETE f", {"p": path})
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
