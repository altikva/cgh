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
import sys
import time
from pathlib import Path

import kuzu

from .schema import init_schema

_DB_DIR = ".codegraph"
_DB_FILE = "graph.db"

# Module-level singletons — one DB + connection per process
_db: kuzu.Database | None = None
_conn: kuzu.Connection | None = None
_ro_db: kuzu.Database | None = None
_ro_conn: kuzu.Connection | None = None

_atexit_registered = False


def get_db_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / _DB_DIR / _DB_FILE


def get_connection(repo_root: str | Path | None = None) -> kuzu.Connection:
    """
    Return (and cache) a read-write Kuzu connection.
    Retries on lock with backoff.
    """
    global _db, _conn, _atexit_registered

    if _conn is not None:
        return _conn

    # Ensure we release the lock on process exit (SIGTERM, etc.)
    if not _atexit_registered:
        atexit.register(reset_connection)
        _atexit_registered = True

    root = Path(repo_root) if repo_root else Path.cwd()
    db_dir = root / _DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / _DB_FILE

    retries = 3
    for attempt in range(retries):
        try:
            _db = kuzu.Database(str(db_path))
            _conn = kuzu.Connection(_db)
            init_schema(_conn)
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


def get_readonly_connection(repo_root: str | Path | None = None) -> kuzu.Connection | None:
    """
    Try to open a read-only Kuzu connection.
    Returns None if the DB is locked — caller should handle gracefully.
    """
    global _ro_db, _ro_conn

    if _ro_conn is not None:
        return _ro_conn

    root = Path(repo_root) if repo_root else Path.cwd()
    db_path = root / _DB_DIR / _DB_FILE

    if not db_path.exists():
        return None

    try:
        _ro_db = kuzu.Database(str(db_path), read_only=True)
        _ro_conn = kuzu.Connection(_ro_db)
        return _ro_conn
    except RuntimeError:
        # Kuzu locks even in read_only mode — return None so caller can degrade
        return None


def reset_connection() -> None:
    """
    Release the Kuzu file lock and force re-open on next call.

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
