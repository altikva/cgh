# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Database connection manager for the local code graph.

from __future__ import annotations

import atexit
import sys
from pathlib import Path

from codegraph.core.protocol import GraphDB

_DB_DIR = ".codegraph"
_DUCKDB_FILE = "graph.duckdb"
_SQLITE_FILE = "graph.sqlite"


def _backend(repo_root: str | Path | None = None) -> str:
    """Pick which graph backend to use for ``repo_root``.

    Resolution order:
      1. CGH_DB env var if set (``duckdb`` or ``sqlite``).
      2. Auto-detect from the files actually present in ``.codegraph/``:
         ``graph.duckdb`` -> duckdb, ``graph.sqlite`` -> sqlite.
      3. Fresh repo: DuckDB when its native library is importable,
         otherwise SQLite. This makes the same code adapt to how it was
         installed: a pip/uvx install bundles DuckDB and defaults to it,
         while the standalone binary ships SQLite-only (no ~50 MB DuckDB
         lib) and defaults to SQLite. Neither needs a build flag.

    Repos with an existing on-disk DB keep it (via step 2) so nothing
    breaks.
    """
    import os

    env_value = (os.environ.get("CGH_DB") or "").strip().lower()
    if env_value in ("duckdb", "sqlite"):
        return env_value

    if repo_root is not None:
        detected = detect_backend_file(repo_root)
        if detected is not None:
            return detected[0]

    return "duckdb" if duckdb_available() else "sqlite"


def duckdb_available() -> bool:
    """True if the DuckDB native library can be imported. False in the
    SQLite-only standalone binary, which is what flips the fresh-repo
    default to SQLite there."""
    import importlib.util

    return importlib.util.find_spec("duckdb") is not None


# Connection caches, keyed by resolved repo root: one process can
# touch several repos (federation, tests, SDK embedding), and a single
# first-caller-wins global handed repo A's connection to repo B.
# DuckDB and SQLite connections are self-contained, so the GraphDB
# adapter is all we cache; close() on it releases everything.
_conns: dict[str, GraphDB] = {}
_ro_conns: dict[str, GraphDB] = {}


def _cache_key(repo_root: str | Path | None) -> str:
    return str((Path(repo_root) if repo_root else Path.cwd()).resolve())


_atexit_registered = False


def detect_backend_file(repo_root: str | Path) -> tuple[str, Path] | None:
    """('duckdb' | 'sqlite', db_file) for whichever graph DB exists in
    ``repo_root/.codegraph/``. DuckDB wins when both are present.
    None when no graph DB is present. The single tie-break authority:
    federation, the status commands and the connection cache all call
    this instead of re-implementing the rule."""
    cg = Path(repo_root) / _DB_DIR
    duck = cg / _DUCKDB_FILE
    if duck.exists():
        return ("duckdb", duck)
    lite = cg / _SQLITE_FILE
    if lite.exists():
        return ("sqlite", lite)
    return None


def open_graphdb_file_ro(backend: str, db_file: str | Path) -> GraphDB | None:
    """Open one graph DB file read-only, uncached, degrading to None
    (locked, corrupt). The shared low-level factory: federation uses it
    on child repos, the status commands on their own repo; the cached
    per-repo path is get_readonly_connection. The caller owns close()."""
    if backend == "sqlite":
        from codegraph.core.db_sqlite import SQLiteGraphDB

        try:
            return SQLiteGraphDB(str(db_file), read_only=True)
        except Exception:
            return None
    from codegraph.core.db_duckdb import DuckDBGraphDB

    try:
        return DuckDBGraphDB(str(db_file), read_only=True)
    except Exception:
        return None


def get_db_path(repo_root: str | Path) -> Path:
    """Return the DB file path for the active backend, auto-detected from
    what's on disk under ``repo_root`` when CGH_DB isn't set."""
    backend = _backend(repo_root)
    fname = _SQLITE_FILE if backend == "sqlite" else _DUCKDB_FILE
    return Path(repo_root) / _DB_DIR / fname


def get_connection(repo_root: str | Path | None = None) -> GraphDB:
    """
    Return (and cache) a read-write GraphDB connection.

    The backend is chosen by the CGH_DB env var ("duckdb" or "sqlite"),
    otherwise auto-detected from disk, otherwise DuckDB when available.
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
    obj = _ro_conns.pop(key, None)
    if obj is not None:
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

    if _backend(root) == "sqlite":
        from codegraph.core.db_sqlite import SQLiteGraphDB

        db_path = db_dir / _SQLITE_FILE
        conn = SQLiteGraphDB(str(db_path), read_only=False)
        _conns[key] = conn
        return conn

    from codegraph.core.db_duckdb import DuckDBGraphDB

    db_path = db_dir / _DUCKDB_FILE
    conn = DuckDBGraphDB(str(db_path), read_only=False)
    _conns[key] = conn
    return conn


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

    if _backend(root) == "sqlite":
        from codegraph.core.db_sqlite import SQLiteGraphDB

        db_path = root / _DB_DIR / _SQLITE_FILE
        if not db_path.exists():
            return None
        try:
            conn = SQLiteGraphDB(str(db_path), read_only=True)
            _ro_conns[key] = conn
            return conn
        except Exception:
            return None

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
        # as "fall through to None" so callers degrade gracefully.
        return None


def reset_connection(repo_root: str | Path | None = None) -> None:
    """
    Release the underlying DB file locks and force re-open on next call.
    With ``repo_root``, only that repo's connections close; without it,
    every cached connection closes (atexit, owner shutdown).
    """
    keys = [_cache_key(repo_root)] if repo_root else None
    for cache in (_conns, _ro_conns):
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
