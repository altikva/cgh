# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read-only federation across a parent repo and its subrepos.
#              The parent indexes only files outside subrepo paths; queries
#              iterate parent + each subrepo, opening their `.codegraph/`
#              databases read-only and aggregating results with a `scope`
#              tag so callers can tell which repo a result came from.

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codegraph.core.config import load_config
from codegraph.core.protocol import GraphDB

_DB_DIR = ".codegraph"
_KUZU_FILE = "graph.db"
_DUCKDB_FILE = "graph.duckdb"
_FTS_FILE = "fts.db"


def _detect_backend_file(repo_root: Path) -> tuple[str, Path] | None:
    """Return ('duckdb' | 'kuzu', file_path) for whichever graph DB exists
    in ``repo_root/.codegraph/``. DuckDB wins when both are present so a
    half-migrated repo (Kuzu cached + new DuckDB) reads the new one.
    Returns None when no graph DB is present.
    """
    cg = repo_root / _DB_DIR
    duck_path = cg / _DUCKDB_FILE
    kuzu_path = cg / _KUZU_FILE
    if duck_path.exists():
        return ("duckdb", duck_path)
    if kuzu_path.exists():
        return ("kuzu", kuzu_path)
    return None


# ---------------------------------------------------------------------------
# Subrepo resolution
# ---------------------------------------------------------------------------


@dataclass
class ChildStatus:
    """Verification result for one declared subrepo."""

    path: Path
    exists: bool
    initialized: bool  # has a .codegraph/ dir
    has_kuzu: bool  # graph.db present
    has_duckdb: bool  # graph.duckdb present
    has_fts: bool  # fts.db present
    is_git_repo: bool  # has a .git dir (informational)
    error: str | None = None  # populated when something failed

    @property
    def has_graphdb(self) -> bool:
        return self.has_kuzu or self.has_duckdb

    @property
    def ok(self) -> bool:
        return self.exists and self.initialized and self.has_graphdb and not self.error


def resolve_children(repo_root: str | Path) -> list[Path]:
    """
    Return absolute, resolved subrepo paths declared in config.toml.

    Skips paths that don't exist on disk (caller can use `verify_child` to
    surface those). Order preserved from config.
    """
    root = Path(repo_root).resolve()
    cfg = load_config(root)
    out: list[Path] = []
    seen: set[Path] = set()
    for entry in cfg.subrepos:
        p = Path(entry).expanduser()
        if not p.is_absolute():
            p = root / p
        try:
            p = p.resolve()
        except OSError:
            continue
        if p == root or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def child_paths_to_skip(repo_root: str | Path) -> list[Path]:
    """
    Paths the parent indexer / watcher must NOT touch, every declared
    subrepo, regardless of whether it's currently initialized. We skip
    even un-initialized ones so adding a subrepo later doesn't require
    re-indexing the parent.
    """
    return resolve_children(repo_root)


def is_under_any(path: str | Path, roots: list[Path]) -> bool:
    """Return True if `path` lives under any of `roots` (inclusive).

    Both sides are resolved and case-normalized before comparison. This matters
    on Windows: the filesystem is case-insensitive and `resolve()` may change
    casing or 8.3 short-names, while a candidate built as `root / "a/b.tf"` from
    a git ls-files path mixes separators. The previous version only resolved the
    roots (via resolve_children) and left an already-absolute candidate
    untouched, so on Windows the case/short-name mismatch made every federated
    subrepo fail to match, and none of their files were skipped from the parent
    scan. pathlib's relative_to is case-sensitive, so we compare normcase'd
    strings on a path-separator boundary instead.
    """
    if not roots:
        return False

    def _norm(pp: Path) -> str:
        try:
            pp = pp.resolve()
        except OSError:
            pp = pp.absolute()
        return os.path.normcase(str(pp))

    p_norm = _norm(Path(path))
    for root in roots:
        r_norm = _norm(Path(root))
        if p_norm == r_norm or p_norm.startswith(r_norm + os.sep):
            return True
    return False


def verify_child(child_path: str | Path) -> ChildStatus:
    """Inspect a subrepo path, return what's present / missing."""
    p = Path(child_path).expanduser()
    if not p.is_absolute():
        # Caller is responsible for absoluting. We don't know the parent root here.
        p = p.resolve()
    if not p.exists():
        return ChildStatus(
            path=p,
            exists=False,
            initialized=False,
            has_kuzu=False,
            has_duckdb=False,
            has_fts=False,
            is_git_repo=False,
            error="path does not exist",
        )

    cg = p / _DB_DIR
    return ChildStatus(
        path=p,
        exists=True,
        initialized=cg.is_dir(),
        has_kuzu=(cg / _KUZU_FILE).exists(),
        has_duckdb=(cg / _DUCKDB_FILE).exists(),
        has_fts=(cg / _FTS_FILE).exists(),
        is_git_repo=(p / ".git").exists(),
    )


# ---------------------------------------------------------------------------
# Read-only connections, fresh per call, NOT cached. Federation calls these
# many times per query (one per subrepo) and connections must be released so
# subrepo owners can keep writing.
# ---------------------------------------------------------------------------


@contextmanager
def open_graphdb_ro(repo_root: Path) -> Iterator[GraphDB | None]:
    """
    Open a fresh read-only GraphDB connection for ``repo_root``, regardless
    of which backend the child repo uses. Detection looks at the file
    actually on disk: ``graph.duckdb`` -> DuckDB, ``graph.db`` -> Kuzu.

    Yields None when the DB is missing, locked, or unreadable. Always
    closes the connection, Kuzu holds an OS file lock that must be
    released so the child's own owner can keep writing.
    """
    detected = _detect_backend_file(repo_root)
    if detected is None:
        yield None
        return
    backend, _ = detected

    if backend == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        db_path = repo_root / _DB_DIR / _DUCKDB_FILE
        conn: GraphDB | None = None
        try:
            try:
                conn = DuckDBGraphDB(str(db_path), read_only=True)
            except Exception:
                yield None
                return
            yield conn
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        return

    # Kuzu path. Kuzu is an optional extra since v0.4.2, a subrepo can
    # be on Kuzu while this (parent) install has no kuzu package. Degrade
    # to None rather than crashing the whole federated query.
    try:
        import kuzu
    except ImportError:
        yield None
        return

    from codegraph.core.db_kuzu import KuzuGraphDB

    db_path = repo_root / _DB_DIR / _KUZU_FILE
    db = None
    raw = None
    try:
        try:
            db = kuzu.Database(str(db_path), read_only=True)
            raw = kuzu.Connection(db)
        except RuntimeError:
            # Locked or schema mismatch, caller should treat as "skipped"
            yield None
            return
        yield KuzuGraphDB(raw)
    finally:
        try:
            if raw is not None:
                raw.close()
        except Exception:
            pass
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


# Backward-compat alias for callers that import the old name. New code
# should prefer open_graphdb_ro.
open_kuzu_ro = open_graphdb_ro


@contextmanager
def open_fts_ro(repo_root: Path) -> Iterator[sqlite3.Connection | None]:
    """
    Open a read-only SQLite (FTS) connection for `repo_root`. Yields None
    if the file is missing.
    """
    db_path = repo_root / _DB_DIR / _FTS_FILE
    if not db_path.exists():
        yield None
        return
    conn = None
    try:
        # mode=ro requires URI form. immutable=0 because subrepos can be
        # written to by their own owners while we read.
        from codegraph.core.utils import ro_sqlite_uri

        conn = sqlite3.connect(
            ro_sqlite_uri(db_path), uri=True, check_same_thread=False
        )
        yield conn
    except sqlite3.Error:
        yield None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Federation iterators
# ---------------------------------------------------------------------------


def iter_db_roots(repo_root: str | Path) -> list[Path]:
    """
    Return [parent_root, *initialized_subrepo_roots]. Subrepos that don't
    have a `.codegraph/graph.db` yet are skipped (with no error).
    """
    parent = Path(repo_root).resolve()
    roots = [parent]
    for child in resolve_children(parent):
        st = verify_child(child)
        if st.ok:
            roots.append(child)
    return roots


@dataclass
class ScopedResult:
    """A federated tool result tagged with the repo it came from."""

    scope: str  # repo display name (e.g. "parent", "child1")
    scope_path: Path  # absolute path of the source repo
    payload: Any  # whatever the per-scope function returned
    error: str | None = None


def _scope_name(repo_root: Path, parent: Path) -> str:
    """Short label for a repo, 'parent' for the root, basename otherwise."""
    if repo_root == parent:
        return "parent"
    return repo_root.name


def for_each_graphdb(
    repo_root: str | Path,
    fn: Callable[[GraphDB, Path], Any],
) -> list[ScopedResult]:
    """
    Run ``fn(conn, scope_path)`` against the graph DB of the parent and
    each initialized subrepo. Failures are captured per-scope. Tries to
    RO-open the parent, only works when no other process holds the
    write lock. For tools running inside the parent's owner (which
    already holds a write conn), use ``for_each_child_graphdb`` instead
    and call your local write conn for the parent scope yourself.
    """
    parent = Path(repo_root).resolve()
    return [_run_one_graphdb(root, parent, fn) for root in iter_db_roots(parent)]


def for_each_child_graphdb(
    repo_root: str | Path,
    fn: Callable[[GraphDB, Path], Any],
) -> list[ScopedResult]:
    """
    Children-only iteration. The caller (typically a parent-owner MCP tool)
    is expected to have already queried its own DB and is now fanning out
    to the subrepos to aggregate cross-repo results.
    """
    parent = Path(repo_root).resolve()
    return [_run_one_graphdb(root, parent, fn) for root in iter_db_roots(parent)[1:]]


def _run_one_graphdb(
    root: Path,
    parent: Path,
    fn: Callable[[GraphDB, Path], Any],
) -> ScopedResult:
    scope = _scope_name(root, parent)
    with open_graphdb_ro(root) as conn:
        if conn is None:
            return ScopedResult(
                scope=scope,
                scope_path=root,
                payload=None,
                error="db unavailable (missing or locked)",
            )
        try:
            payload = fn(conn, root)
            return ScopedResult(scope=scope, scope_path=root, payload=payload)
        except Exception as exc:
            return ScopedResult(
                scope=scope,
                scope_path=root,
                payload=None,
                error=f"{type(exc).__name__}: {exc}",
            )


def federate_scoped(
    get_parent_conn: Callable[[], GraphDB],
    repo_root: str | Path | None,
    query_fn: Callable[[GraphDB], Any],
) -> tuple[list[tuple[str, list]], list[dict]]:
    """Run ``query_fn(conn)`` against the parent's in-process write conn and
    each federated child's RO graph DB.

    Returns ``(scoped, warnings)`` where ``scoped`` is ``[(scope, payload), …]``
    (scope "parent" first, then each child by name) and ``warnings`` is
    ``[{scope, error}, …]`` for any scope whose query failed.

    This is the one place the parent+children fan-out lives; MCP tool modules
    call it instead of each re-implementing the loop.
    """
    scoped: list[tuple[str, list]] = []
    warnings: list[dict] = []
    try:
        scoped.append(("parent", query_fn(get_parent_conn()) or []))
    except Exception as exc:
        warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})
    if repo_root is not None:
        for s in for_each_child_graphdb(repo_root, lambda c, _r: query_fn(c)):
            if s.error:
                warnings.append({"scope": s.scope, "error": s.error})
                continue
            scoped.append((s.scope, s.payload or []))
    return scoped, warnings


def federate_flat(
    get_parent_conn: Callable[[], GraphDB],
    repo_root: str | Path | None,
    query_fn: Callable[[GraphDB], Any],
) -> tuple[list[dict], list[dict]]:
    """Flattened view of :func:`federate_scoped`: every payload row gets a
    ``"scope"`` key and all rows land in one list. Returns ``(rows, warnings)``."""
    scoped, warnings = federate_scoped(get_parent_conn, repo_root, query_fn)
    rows: list[dict] = []
    for scope, payload in scoped:
        for item in payload:
            item["scope"] = scope
            rows.append(item)
    return rows, warnings


# Backward-compat aliases for callers that import the old names. New
# code should prefer the _graphdb variants, these will be removed in
# the 0.6 release that also deletes the Kuzu-specific code paths.
for_each_kuzu = for_each_graphdb
for_each_child_kuzu = for_each_child_graphdb
_run_one_kuzu = _run_one_graphdb


def for_each_fts(
    repo_root: str | Path,
    fn: Callable[[sqlite3.Connection, Path], Any],
) -> list[ScopedResult]:
    """Same as for_each_graphdb but for the FTS sqlite databases."""
    parent = Path(repo_root).resolve()
    return [_run_one_fts(root, parent, fn) for root in iter_db_roots(parent)]


def for_each_child_fts(
    repo_root: str | Path,
    fn: Callable[[sqlite3.Connection, Path], Any],
) -> list[ScopedResult]:
    """Children-only FTS iteration, the parent caller uses its cached conn."""
    parent = Path(repo_root).resolve()
    return [_run_one_fts(root, parent, fn) for root in iter_db_roots(parent)[1:]]


def _run_one_fts(
    root: Path,
    parent: Path,
    fn: Callable[[sqlite3.Connection, Path], Any],
) -> ScopedResult:
    scope = _scope_name(root, parent)
    with open_fts_ro(root) as conn:
        if conn is None:
            return ScopedResult(
                scope=scope,
                scope_path=root,
                payload=None,
                error="fts db unavailable",
            )
        try:
            payload = fn(conn, root)
            return ScopedResult(scope=scope, scope_path=root, payload=payload)
        except Exception as exc:
            return ScopedResult(
                scope=scope,
                scope_path=root,
                payload=None,
                error=f"{type(exc).__name__}: {exc}",
            )


def has_subrepos(repo_root: str | Path) -> bool:
    """Cheap check, useful for tools to take a fast no-op path when off."""
    return bool(resolve_children(repo_root))


@dataclass
class OwnerStatus:
    """Per-child owner liveness, used by `cgh federate list/up/down`."""

    alive: bool
    pid: int | None
    port: int | None


def child_owner_status(child_path: str | Path) -> OwnerStatus:
    """Inspect whether a federated child has its own MCP owner running."""
    from codegraph.state.ipc import is_owner_alive, read_owner_pid, read_owner_port

    p = Path(child_path)
    return OwnerStatus(
        alive=is_owner_alive(p),
        pid=read_owner_pid(p),
        port=read_owner_port(p),
    )


# ---------------------------------------------------------------------------
# Config mutation helpers (used by the CLI)
# ---------------------------------------------------------------------------


def add_subrepo(
    repo_root: str | Path, child_path: str | Path
) -> tuple[Path, ChildStatus]:
    """
    Append `child_path` to the parent's config.toml and return its status.
    Idempotent, if already present, just returns the current status.
    Raises ValueError if the child path doesn't exist.
    """
    parent = Path(repo_root).resolve()
    child = Path(child_path).expanduser()
    if not child.is_absolute():
        child = (parent / child).resolve()
    else:
        child = child.resolve()

    if not child.exists():
        raise ValueError(f"subrepo path does not exist: {child}")
    if child == parent:
        raise ValueError("subrepo cannot be the parent itself")

    # Store as relative when it's under the parent, else absolute. Always use
    # forward slashes: str(Path) yields backslashes on Windows, and a backslash
    # in a TOML string is an escape (e.g. \s is invalid), which would corrupt
    # config.toml so it no longer parses. Forward slashes are portable and
    # Path accepts them on every platform.
    try:
        rel = child.relative_to(parent)
        stored = "./" + rel.as_posix()
    except ValueError:
        stored = child.as_posix()

    cfg_path = parent / _DB_DIR / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_config_toml(cfg_path)
    cg = existing.setdefault("codegraph", {})
    subs = list(cg.get("subrepos", []))
    if stored not in subs:
        subs.append(stored)
        cg["subrepos"] = subs
        _write_config_toml(cfg_path, existing)

    return child, verify_child(child)


def remove_subrepo(repo_root: str | Path, child_path: str | Path) -> bool:
    """Remove a subrepo from config.toml. Returns True if removed."""
    parent = Path(repo_root).resolve()
    child = Path(child_path).expanduser()
    if not child.is_absolute():
        child = (parent / child).resolve()
    else:
        child = child.resolve()

    cfg_path = parent / _DB_DIR / "config.toml"
    if not cfg_path.exists():
        return False
    existing = _read_config_toml(cfg_path)
    cg = existing.get("codegraph", {})
    subs = list(cg.get("subrepos", []))

    # Match either the stored form or the resolved absolute form
    new_subs = []
    removed = False
    for entry in subs:
        p = Path(entry).expanduser()
        if not p.is_absolute():
            p = (parent / p).resolve()
        else:
            p = p.resolve()
        if p == child:
            removed = True
            continue
        new_subs.append(entry)

    if removed:
        cg["subrepos"] = new_subs
        existing["codegraph"] = cg
        _write_config_toml(cfg_path, existing)
    return removed


# ---------------------------------------------------------------------------
# TOML I/O, minimal, preserves only what we touch (codegraph table). Other
# tables are pass-through. We don't pull in tomli-w to keep deps light.
# ---------------------------------------------------------------------------


def _read_config_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib

        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _write_config_toml(path: Path, data: dict) -> None:
    """Re-emit the config in a stable, hand-written-friendly TOML form."""
    lines: list[str] = []
    cg = data.get("codegraph", {})
    if cg:
        lines.append("[codegraph]")
        for key, value in cg.items():
            lines.append(_emit_toml_value(key, value))
        lines.append("")

    # Pass-through other top-level tables (parsers, mcp, ruflo, paths, …)
    for table, body in data.items():
        if table == "codegraph":
            continue
        if not isinstance(body, dict):
            continue
        lines.append(f"[{table}]")
        for key, value in body.items():
            lines.append(_emit_toml_value(key, value))
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_escape(s: str) -> str:
    """Escape a string for a TOML basic ("...") value: backslash first, then
    the double quote. Without this, a Windows path or any value with a
    backslash produces invalid TOML."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _emit_toml_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, str):
        return f'{key} = "{_toml_escape(value)}"'
    if isinstance(value, list):
        items = ", ".join(_format_scalar(v) for v in value)
        return f"{key} = [{items}]"
    return f'{key} = "{_toml_escape(str(value))}"'


def _format_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return f'"{_toml_escape(str(v))}"'
