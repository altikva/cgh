# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
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
    "The Kuzu graph backend is selected but the `kuzu` package is not installed. "
    "Install it with `pip install cgh[kuzu]` (or `uv tool install cgh --with kuzu`), "
    "or convert this repo to DuckDB by running `cgh migrate-to-duckdb`. "
    "DuckDB is the default backend since v0.4 — see docs/CONFIGURATION.md."
)


def _import_kuzu():
    """Import kuzu lazily with a friendly error when it's missing.

    Kuzu became an optional dependency in v0.4.2 so cp3.14 users could
    install cgh (Kuzu has no cp3.14 wheels yet). Anything that needs
    the Kuzu backend resolves it through this helper.
    """
    try:
        import kuzu as _kuzu
    except ImportError as exc:
        raise RuntimeError(_KUZU_MISSING_MSG) from exc
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
        cg = Path(repo_root) / _DB_DIR
        if (cg / _DUCKDB_FILE).exists():
            return "duckdb"
        if (cg / _DB_FILE).exists():
            return "kuzu"

    return "duckdb"

# Module-level singletons — one DB + connection per process.
# _db / _ro_db stay as raw kuzu.Database refs so reset_connection() can
# close them explicitly (Kuzu's file lock outlives the GC otherwise).
# _conn / _ro_conn are the GraphDB-typed adapters callers see.
# _db / _ro_db hold raw kuzu.Database refs (typed as Any so the module
# can import without kuzu installed). DuckDB doesn't need a separate
# "db" handle — its connection is self-contained.
_db: Any | None = None
_conn: GraphDB | None = None
_ro_db: Any | None = None
_ro_conn: GraphDB | None = None

_atexit_registered = False


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
    global _db, _conn, _ro_db, _ro_conn, _atexit_registered

    if _conn is not None:
        return _conn

    # DuckDB refuses to open a RW connection in a process that already
    # holds a RO connection to the same file ("Can't open a connection
    # to same database file with a different configuration"). cgh init
    # hits this: it opens RO for the existing-state probe, then asks
    # for RW to index. Close any cached RO conn before opening RW so
    # both backends behave consistently.
    if _ro_conn is not None or _ro_db is not None:
        for obj in (_ro_conn, _ro_db):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
        _ro_conn = None
        _ro_db = None

    # Ensure we release the lock on process exit (SIGTERM, etc.)
    if not _atexit_registered:
        atexit.register(reset_connection)
        _atexit_registered = True

    root = Path(repo_root) if repo_root else Path.cwd()
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)

    if _backend(root) == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        db_path = db_dir / _DUCKDB_FILE
        _conn = DuckDBGraphDB(str(db_path), read_only=False)
        return _conn

    db_path = db_dir / _DB_FILE

    kuzu = _import_kuzu()
    from codegraph.core.db_kuzu import KuzuGraphDB
    from codegraph.core.schema import init_schema

    retries = 3
    for attempt in range(retries):
        try:
            _db = kuzu.Database(str(db_path))
            raw_conn = kuzu.Connection(_db)
            init_schema(raw_conn)
            _conn = KuzuGraphDB(raw_conn)
            return _conn
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
    Returns None if the DB is locked or absent — caller should handle gracefully.
    """
    global _ro_db, _ro_conn

    if _ro_conn is not None:
        return _ro_conn

    # Same-process RO+RW on the same file is rejected by DuckDB. If a
    # RW connection is already cached, hand it back — every GraphDB
    # method we call from "readonly" callers is a pure read, so this is
    # safe and avoids the connection conflict.
    if _conn is not None:
        return _conn

    root = Path(repo_root) if repo_root else Path.cwd()

    if _backend(root) == "duckdb":
        from codegraph.core.db_duckdb import DuckDBGraphDB

        db_path = root / _DB_DIR / _DUCKDB_FILE
        if not db_path.exists():
            return None
        try:
            _ro_conn = DuckDBGraphDB(str(db_path), read_only=True)
            return _ro_conn
        except Exception:
            # DuckDB raises a few different exception classes depending on
            # what's wrong (locked, corrupt, version mismatch). Treat all
            # as "fall through to None" so callers degrade gracefully —
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
        _ro_db = kuzu.Database(str(db_path), read_only=True)
        raw_conn = kuzu.Connection(_ro_db)
        _ro_conn = KuzuGraphDB(raw_conn)
        return _ro_conn
    except RuntimeError:
        # Kuzu locks even in read_only mode — return None so caller can degrade
        return None


def reset_connection() -> None:
    """
    Release the underlying DB file lock and force re-open on next call.

    Kuzu holds an OS-level write lock for the lifetime of the Database
    object. Dropping Python references alone is not enough — CPython's
    GC may defer destruction, and the lock lingers until the process
    exits. We explicitly close the Connection + Database here so the
    lock is released immediately.
    """
    global _db, _conn, _ro_db, _ro_conn
    for obj in (_conn, _db, _ro_conn, _ro_db):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:
            pass
    _conn = None
    _db = None
    _ro_conn = None
    _ro_db = None
