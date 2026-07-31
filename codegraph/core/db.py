# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Database connection manager for the local Kuzu code graph.

from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path
from typing import Any

from codegraph.core.protocol import GraphDB

_DB_DIR = ".codegraph"
_DB_FILE = "graph.db"
_DUCKDB_FILE = "graph.duckdb"

_KUZU_MISSING_MSG = (
    "This repo uses the Kuzu graph backend (it has a .codegraph/graph.db), "
    "but the kuzu package is not installed. Pick one of these.\n"
    "\n"
    "Keep Kuzu, install the extra:\n"
    "    uv tool install cgh --with kuzu\n"
    "    pip install cgh[kuzu]\n"
    "\n"
    "Move to DuckDB (the default since v0.4, no extra package):\n"
    "    cgh migrate-to-duckdb\n"
    "\n"
    "Or start fresh on DuckDB:\n"
    "    rm .codegraph/graph.db && cgh index"
)


class KuzuNotInstalled(RuntimeError):
    """Raised when the Kuzu backend is selected but the `kuzu` package
    is not importable. Carries the remediation text in its message. The
    CLI catches this at the top level and prints a clean message instead
    of a traceback (unless --verbose). See codegraph/__main__.py."""


def _import_kuzu():
    """Import kuzu lazily with a friendly error when it's missing.

    Kuzu became an optional dependency in v0.4.2 so cp3.14 users could
    install cgh (Kuzu has no cp3.14 wheels yet). Anything that needs
    the Kuzu backend resolves it through this helper.
    """
    try:
        import kuzu as _kuzu
    except ImportError as exc:
        raise KuzuNotInstalled(_KUZU_MISSING_MSG) from exc
    return _kuzu


def _backend(repo_root: str | Path | None = None) -> str:
    """Pick which graph backend to use for ``repo_root``.

    Resolution order:
      1. CGH_DB env var if set (``duckdb`` or ``kuzu``).
      2. Auto-detect from the files actually present in ``.codegraph/``:
         ``graph.duckdb`` -> duckdb, ``graph.db`` -> kuzu.
      3. Fall back to duckdb for a brand-new (no .codegraph/) repo.

    The fresh-repo default flipped from kuzu to duckdb in the 0.5
    cycle. Repos with an existing ``graph.db`` keep being read as
    Kuzu (via step 2) so existing installs aren't broken; the
    `cgh init` auto-migration handles the transition.
    """
    env_value = (os.environ.get("CGH_DB") or "").strip().lower()
    if env_value in ("duckdb", "kuzu"):
        return env_value

    if repo_root is not None:
        detected = detect_backend_file(repo_root)
        if detected is not None:
            return detected[0]

    return "duckdb"


# Connection caches, keyed by resolved repo root: one process can
# touch several repos (federation, tests, SDK embedding), and a single
# first-caller-wins global handed repo A's connection to repo B.
# _dbs / _ro_dbs hold raw kuzu.Database refs so the reset can close
# them explicitly (Kuzu's file lock outlives the GC otherwise; typed
# Any so the module imports without kuzu). DuckDB's connection is
# self-contained.
_conns: dict[str, GraphDB] = {}
_dbs: dict[str, Any] = {}
_ro_conns: dict[str, GraphDB] = {}
_ro_dbs: dict[str, Any] = {}


def _cache_key(repo_root: str | Path | None) -> str:
    return str((Path(repo_root) if repo_root else Path.cwd()).resolve())


_atexit_registered = False


def detect_backend_file(repo_root: str | Path) -> "tuple[str, Path] | None":
    """('duckdb' | 'kuzu', db_file) for whichever graph DB exists in
    ``repo_root/.codegraph/``. DuckDB wins when both are present so a
    half-migrated repo (Kuzu cached + new DuckDB) reads the new one.
    None when no graph DB is present. The single tie-break authority:
    federation, the status commands and the connection cache all call
    this instead of re-implementing the rule."""
    cg = Path(repo_root) / _DB_DIR
    duck = cg / _DUCKDB_FILE
    if duck.exists():
        return ("duckdb", duck)
    kz = cg / _DB_FILE
    if kz.exists():
        return ("kuzu", kz)
    return None


def open_graphdb_file_ro(backend: str, db_file: str | Path) -> GraphDB | None:
    """Open one graph DB file read-only, uncached, degrading to None
    (locked, corrupt, kuzu not installed). The shared low-level factory:
    federation uses it on child repos, the status commands on their own
    repo; the cached per-repo path is get_readonly_connection. The
    caller owns close()."""
    if backend == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        try:
            return DuckDBGraphDB(str(db_file), read_only=True)
        except Exception:
            return None
    try:
        kuzu = _import_kuzu()
    except KuzuNotInstalled:
        return None
    from codegraph.core.db_kuzu import KuzuGraphDB

    try:
        db = kuzu.Database(str(db_file), read_only=True)
        conn = KuzuGraphDB(kuzu.Connection(db))
        conn._db_handle = db  # close() releases the file lock too
        return conn
    except RuntimeError:
        return None


def get_db_path(repo_root: str | Path) -> Path:
    """Return the DB file path for the active backend, auto-detected from
    what's on disk under ``repo_root`` when CGH_DB isn't set."""
    fname = _DUCKDB_FILE if _backend(repo_root) == "duckdb" else _DB_FILE
    return Path(repo_root) / _DB_DIR / fname


def get_connection(repo_root: str | Path | None = None) -> GraphDB:
    """
    Return (and cache) a read-write GraphDB connection.
    Retries on lock with backoff.

    The backend is chosen by the CGH_DB env var: "duckdb" for the new
    backend (work in progress), anything else for Kuzu (default).
    """
    global _atexit_registered

    key = _cache_key(repo_root)
    cached = _conns.get(key)
    if cached is not None:
        return cached

    # DuckDB refuses to open a RW connection in a process that already
    # holds a RO connection to the same file ("Can't open a connection
    # to same database file with a different configuration"). cgh init
    # hits this: it opens RO for the existing-state probe, then asks
    # for RW to index. Close any cached RO conn for THIS repo before
    # opening RW so both backends behave consistently.
    for cache in (_ro_conns, _ro_dbs):
        obj = cache.pop(key, None)
        if obj is None:
            continue
        try:
            obj.close()
        except Exception as exc:
            # A failed close here resurfaces later as a confusing
            # same-process open conflict; name the real cause now
            # (reset_connection logs the same way).
            print(f"[codegraph] warning: RO close before RW open failed: {exc}")

    # Ensure we release the lock on process exit (SIGTERM, etc.)
    if not _atexit_registered:
        atexit.register(reset_connection)
        _atexit_registered = True

    root = Path(key)
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)

    if _backend(root) == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        db_path = db_dir / _DUCKDB_FILE
        conn = DuckDBGraphDB(str(db_path), read_only=False)
        _conns[key] = conn
        return conn

    db_path = db_dir / _DB_FILE

    kuzu = _import_kuzu()
    from codegraph.core.db_kuzu import KuzuGraphDB
    from codegraph.core.schema import init_schema

    retries = 3
    for attempt in range(retries):
        try:
            db = kuzu.Database(str(db_path))
            raw_conn = kuzu.Connection(db)
            init_schema(raw_conn)
            conn = KuzuGraphDB(raw_conn)
            _dbs[key] = db
            _conns[key] = conn
            return conn
        except RuntimeError as exc:
            if "Could not set lock" in str(exc) and attempt < retries - 1:
                wait = 1.0 * (attempt + 1)
                print(
                    f"[codegraph] DB locked, retrying in {wait:.0f}s... ({attempt + 1}/{retries})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if "Could not set lock" in str(exc):
                print(
                    "\n[codegraph] ERROR: Database is locked by another process.\n"
                    "  Try: codegraph stats  (read-only, works while indexing)\n"
                    "  Or:  pkill -f codegraph  to kill the other process\n",
                    file=sys.stderr,
                )
            raise


def get_readonly_connection(repo_root: str | Path | None = None) -> GraphDB | None:
    """
    Try to open a read-only GraphDB connection.
    Returns None if the DB is locked or absent, caller should handle gracefully.
    """
    key = _cache_key(repo_root)
    cached = _ro_conns.get(key)
    if cached is not None:
        return cached

    # Same-process RO+RW on the same file is rejected by DuckDB. If a
    # RW connection is already cached for this repo, hand it back:
    # every GraphDB method "readonly" callers use is a pure read, so
    # this is safe and avoids the connection conflict.
    rw = _conns.get(key)
    if rw is not None:
        return rw

    root = Path(key)

    if _backend(root) == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        db_path = root / _DB_DIR / _DUCKDB_FILE
        if not db_path.exists():
            return None
        try:
            conn = DuckDBGraphDB(str(db_path), read_only=True)
            _ro_conns[key] = conn
            return conn
        except Exception:
            # DuckDB raises a few different exception classes depending on
            # what's wrong (locked, corrupt, version mismatch). Treat all
            # as "fall through to None" so callers degrade gracefully,
            # symmetric with the Kuzu branch below.
            return None

    db_path = root / _DB_DIR / _DB_FILE
    if not db_path.exists():
        return None

    try:
        kuzu = _import_kuzu()
    except RuntimeError:
        # Kuzu backend selected but package not installed → degrade to
        # None (same shape as a lock failure below). Callers that need
        # an authoritative answer rather than "soft None" should call
        # get_connection(), which raises with the install hint.
        return None
    from codegraph.core.db_kuzu import KuzuGraphDB

    try:
        db = kuzu.Database(str(db_path), read_only=True)
        raw_conn = kuzu.Connection(db)
        conn = KuzuGraphDB(raw_conn)
        _ro_dbs[key] = db
        _ro_conns[key] = conn
        return conn
    except RuntimeError:
        # Kuzu locks even in read_only mode, return None so caller can degrade
        return None


def reset_connection(repo_root: str | Path | None = None) -> None:
    """
    Release the underlying DB file locks and force re-open on next call.
    With ``repo_root``, only that repo's connections close; without it,
    every cached connection closes (atexit, owner shutdown).

    Kuzu holds an OS-level write lock for the lifetime of the Database
    object. Dropping Python references alone is not enough, CPython's
    GC may defer destruction, and the lock lingers until the process
    exits. We explicitly close the Connection + Database here so the
    lock is released immediately.
    """
    keys = [_cache_key(repo_root)] if repo_root else None
    for cache in (_conns, _dbs, _ro_conns, _ro_dbs):
        for key in keys if keys is not None else list(cache):
            obj = cache.pop(key, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception as exc:
                # A close that fails on the owner's shutdown path can leave
                # the file lock lingering; surface it instead of swallowing.
                print(
                    f"[codegraph] warning: failed to close {type(obj).__name__}: {exc}",
                    file=sys.stderr,
                )
