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


def _upsert_file(conn: kuzu.Connection, path: str, lang: str, mtime: float) -> None:
    conn.execute(
        "MERGE (f:File {path: $p}) SET f.lang = $l, f.mtime = $m",
        {"p": path, "l": lang, "m": mtime},
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
    _upsert_file(conn, str(path), lang, mtime)

    # Ingest into graph
    if idx.functions or idx.classes:
        _ingest_code(conn, idx)
    if idx.resources:
        _ingest_terraform(conn, idx)
    if idx.sections:
        _ingest_markdown(conn, idx)

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
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def index_repo(
    repo_root: str | Path,
    verbose: bool = False,
    on_file: Callable[[Path, str, dict], None] | None = None,
    on_discovery: Callable[[int, str], None] | None = None,
) -> dict:
    """
    Walk the repo, index all supported files.
    Uses `git ls-files` to respect .gitignore. Falls back to os.walk if not a git repo.

    Args:
        repo_root: Repository root.
        verbose: Print each file to stdout (ignored if on_file is set).
        on_file: Callback(file_path, status, stats) called after each file.
                 status is "indexed", "skipped", or "error".
        on_discovery: Callback(total_files, method) called once after file discovery.

    Returns a summary dict.
    """
    repo_root = Path(repo_root)
    stats = {"indexed": 0, "skipped": 0, "errors": 0}
    t0 = time.time()

    git_files = _git_tracked_files(repo_root)

    if git_files is not None:
        # Filter to parseable files
        parseable = []
        for full_path in git_files:
            if not is_supported(full_path):
                stats["skipped"] += 1
                continue
            if any(part in _IGNORE_DIRS for part in full_path.parts):
                stats["skipped"] += 1
                continue
            if not full_path.exists():
                stats["skipped"] += 1
                continue
            parseable.append(full_path)

        if on_discovery:
            on_discovery(len(parseable), "git_ls_files")
        elif verbose:
            print(f"  [codegraph] using git ls-files ({len(parseable)} parseable files)")

        for full_path in parseable:
            ok = index_file(full_path, repo_root)
            status = "indexed" if ok else "error"
            if ok:
                stats["indexed"] += 1
            else:
                stats["errors"] += 1

            if on_file:
                on_file(full_path, status, stats)
            elif verbose:
                print(f"  + {full_path.relative_to(repo_root)}")
    else:
        if on_discovery:
            on_discovery(-1, "os_walk")
        elif verbose:
            print("  [codegraph] git not available, falling back to os.walk")

        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
            for filename in filenames:
                full_path = Path(dirpath) / filename
                if not is_supported(full_path):
                    stats["skipped"] += 1
                    continue

                ok = index_file(full_path, repo_root)
                status = "indexed" if ok else "error"
                if ok:
                    stats["indexed"] += 1
                else:
                    stats["errors"] += 1

                if on_file:
                    on_file(full_path, status, stats)
                elif verbose:
                    print(f"  + {full_path.relative_to(repo_root)}")

    stats["elapsed_s"] = round(time.time() - t0, 2)
    stats["method"] = "git_ls_files" if git_files is not None else "os_walk"
    return stats
