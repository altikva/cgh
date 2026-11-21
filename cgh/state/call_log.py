# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Call logger for cgh MCP tools.
#              Logs every tool invocation with args, result size, latency.
#              Backed by SQLite for persistence and fast aggregation.

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_DB_DIR = ".cgh"
_LOG_FILE = "call_log.db"
# Shared knowledge stores live outside any repo, under the global dir, one
# directory per namespace holding a knowledge.db with the same schema as a
# repo store (knowledge + FTS + outcomes), minus the call_log of tool traces.
_SHARED_SUBDIR = "shared"
_SHARED_FILE = "knowledge.db"

# Who triggered a tool call. "agent": an MCP client through the stdio proxy.
# "cli": a `cgh` command asking the owner over HTTP. "hook": the same, from a
# hook entry point (agent lifecycle hooks, git hooks). "internal": a tool
# function called in-process, outside any HTTP request.
ORIGINS = ("agent", "hook", "cli", "internal")
# HTTP header the proxy and the CLI client send so the owner can tell them apart.
ORIGIN_HEADER = "X-Cgh-Origin"
# Environment variable a hook entry point sets so the owner calls it makes
# are tagged "hook" instead of "cli".
ORIGIN_ENV = "CGH_ORIGIN"


def normalize_origin(value: str | None) -> str | None:
    """A known origin in lower case, or None for anything else."""
    origin = (value or "").strip().lower()
    return origin if origin in ORIGINS else None


def client_origin() -> str:
    """Origin a CLI process announces to the owner: "hook" when a hook entry
    point set CGH_ORIGIN, else "cli"."""
    import os

    return "hook" if normalize_origin(os.environ.get(ORIGIN_ENV)) == "hook" else "cli"


# Keyed by resolved repo root: one process can serve several repos
# (federation, SDK, tests); a first-caller-wins global handed repo A's
# knowledge DB to repo B. Same pattern as state/findings.py.
_conns: dict[str, sqlite3.Connection] = {}

# This connection is shared across asyncio MCP tool threads, watcher threads,
# and the main owner thread. Sqlite3 in Python forbids cross-thread reuse
# unless check_same_thread=False AND callers serialize themselves. This lock
# guards every operation against the shared connection.
_LOG_LOCK = threading.RLock()


def _locked(fn):
    """Serialize all access to the shared sqlite3 connection."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOG_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def _cache_key(repo_root: str | Path | None) -> str:
    return str((Path(repo_root) if repo_root else Path.cwd()).resolve())


def _get_conn(repo_root: str | Path | None = None) -> sqlite3.Connection:
    key = _cache_key(repo_root)
    conn = _conns.get(key)
    if conn is not None:
        return conn

    # Schema bootstrap below races if two threads first-touch concurrently.
    with _LOG_LOCK:
        conn = _conns.get(key)
        if conn is not None:
            return conn
        return _init_conn(repo_root)


# A shared namespace becomes a path segment under ~/.cgh/shared/, so its name
# is held to a strict allowlist: lower-case start, then word / dot / dash, no
# separator, no leading dot, no "..". This is the first line of defence against
# a name like "../../etc" escaping the shared root; shared_store_path then
# resolves the final path and refuses anything outside the root as well.
_VALID_SHARED_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def valid_shared_namespace(name: str) -> bool:
    """Whether ``name`` is a safe shared-store name: matches the allowlist and
    carries no path-traversal token. Fails closed on anything else."""
    n = (name or "").strip()
    return bool(_VALID_SHARED_NAME_RE.match(n)) and ".." not in n and "/" not in n


def shared_store_path(name: str) -> Path:
    """Path of a shared knowledge store: ``~/.cgh/shared/<name>/knowledge.db``.
    GLOBAL_DIR is read at call time so a test that redirects it is honoured.

    The name is validated against a strict allowlist and the resolved path is
    verified to stay under ``~/.cgh/shared``; a traversal attempt raises
    ValueError rather than touching a file outside the shared root."""
    import cgh.core.config as _config

    if not valid_shared_namespace(name):
        raise ValueError(f"invalid shared namespace name: {name!r}")
    root = (_config.GLOBAL_DIR / _SHARED_SUBDIR).resolve()
    path = (root / name / _SHARED_FILE).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"shared namespace escapes the shared root: {name!r}")
    return path


def is_shared_namespace(namespace: str) -> bool:
    """Whether a namespace lives in its own shared store rather than the local
    repo store. ``repo``, ``branch`` and any ``ticket:<id>`` facet stay in the
    repo store; every other non-empty name routes to a shared store. An invalid
    name (fails the shared-name allowlist) is NOT treated as shared, so it can
    never build a shared path."""
    ns = (namespace or "").strip()
    if not ns or ns in ("repo", "branch") or ns.startswith("ticket:"):
        return False
    return valid_shared_namespace(ns)


def _get_conn_at(db_file: str | Path) -> sqlite3.Connection:
    """Cached connection to an explicit knowledge DB file (a shared store),
    built with the same schema as a repo store and keyed by its resolved path."""
    key = str(Path(db_file).resolve())
    conn = _conns.get(key)
    if conn is not None:
        return conn
    with _LOG_LOCK:
        conn = _conns.get(key)
        if conn is not None:
            return conn
        return _init_conn(db_file=db_file)


def reset_for_tests() -> None:
    # Close every cached connection. Test helper, mirrors findings.py.
    with _LOG_LOCK:
        for conn in _conns.values():
            try:
                conn.close()
            except Exception:
                pass
        _conns.clear()


def _init_conn(
    repo_root: str | Path | None = None, *, db_file: str | Path | None = None
) -> sqlite3.Connection:
    # A shared store is addressed by its own db_file; a repo store by its root.
    # Both get the identical schema below; the shared store simply never has its
    # call_log of tool traces written to, only the knowledge tables.
    if db_file is not None:
        db_path = Path(db_file)
        key = str(db_path.resolve())
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        key = _cache_key(repo_root)
        db_path = Path(key) / _DB_DIR / _LOG_FILE
        db_path.parent.mkdir(parents=True, exist_ok=True)

    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS call_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   REAL NOT NULL,
            tool        TEXT NOT NULL,
            args        TEXT NOT NULL DEFAULT '{}',
            latency_ms  REAL NOT NULL DEFAULT 0,
            result_size INTEGER NOT NULL DEFAULT 0,
            success     INTEGER NOT NULL DEFAULT 1,
            error       TEXT
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_log_tool ON call_log(tool)
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_log_ts ON call_log(timestamp)
    """)
    # Session-scoped dedup, track which entities a context_for_task /
    # session_context call has already surfaced for a given session_id.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS session_mentions (
            session_id   TEXT NOT NULL,
            entity_kind  TEXT NOT NULL,
            entity_key   TEXT NOT NULL,
            ts           REAL NOT NULL,
            PRIMARY KEY (session_id, entity_kind, entity_key)
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mentions_session
            ON session_mentions(session_id)
    """)
    # Knowledge store, patterns, decisions, gotchas, style preferences,
    # glossary entries. Explicitly written by Claude via the knowledge_*
    # MCP tools. Backed by an FTS5 virtual table for BM25 search.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            tags        TEXT NOT NULL DEFAULT '',
            kind        TEXT NOT NULL DEFAULT 'note',
            file_refs   TEXT NOT NULL DEFAULT '',
            ts          REAL NOT NULL
        )
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge(kind)
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_session ON knowledge(session_id)
    """)
    # Supersede links: an entry can replace an older one; superseded
    # entries stop appearing in searches, lists and resume bundles but
    # stay queryable by id. Guarded ALTER for databases created before
    # the column existed.
    try:
        _conn.execute("ALTER TABLE knowledge ADD COLUMN superseded_by INTEGER")
    except sqlite3.OperationalError:
        pass  # column already there
    # Promotion columns: a per-ticket worktree learns something and it is
    # promoted into the main checkout's store at merge. `scope` is 'repo' (true
    # for the repo, promotable) or 'branch' (true only on an unmerged branch, not
    # promoted); the source_* columns and the two timestamps carry provenance so
    # a promoted entry keeps its origin instead of looking native. Guarded ALTERs
    # for databases created before the columns existed. source_worktree and
    # origin_id (the entry's id in the source store) make a re-run recognise
    # what it already carried, and let a revised entry replace its earlier copy.
    #
    # Provenance and namespace columns extend the same tolerant pattern:
    # `namespace` is the logical scope a row belongs to and defaults to 'repo'
    # (rows written before the column read back as 'repo'); origin_repo, ticket
    # and author say where a learning came from; symptom holds a raw error
    # signature and symptom_norm its normalised form, for signature-based recall.
    # Rows from before these columns read back with the rest NULL ("unknown"),
    # the same convention as the call_log origin/repo_root columns below. These
    # ALTERs run before the FTS triggers because the triggers reference the new
    # `symptom` column.
    for _col, _type in (
        ("scope", "TEXT"),
        ("source_branch", "TEXT"),
        ("source_commit", "TEXT"),
        ("source_pr", "TEXT"),
        ("source_session", "TEXT"),
        ("origin_ts", "REAL"),
        ("promoted_at", "REAL"),
        ("source_worktree", "TEXT"),
        ("origin_id", "INTEGER"),
        ("namespace", "TEXT NOT NULL DEFAULT 'repo'"),
        ("origin_repo", "TEXT"),
        ("ticket", "TEXT"),
        ("author", "TEXT"),
        ("symptom", "TEXT"),
        ("symptom_norm", "TEXT"),
        # Collision-resistant owner identity of the writing repo, set
        # server-side for a shared-store write (never from caller input). It is
        # the sole authority for a cross-repo supersede; NULL (a repo-store row,
        # or a shared row written before this column) is owned by nobody.
        ("owner", "TEXT"),
    ):
        try:
            _conn.execute(f"ALTER TABLE knowledge ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass  # column already there
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_origin "
        "ON knowledge(origin_id, source_worktree)"
    )
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_namespace ON knowledge(namespace)"
    )
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_symptom ON knowledge(symptom_norm)"
    )
    # FTS mirror of the knowledge table, with `symptom` indexed alongside the
    # text columns. CREATE ... IF NOT EXISTS never alters an existing table, so a
    # store built before symptom was indexed keeps a 4-column FTS: detect the
    # missing column, drop it and let the rebuild below repopulate from content.
    _fts_create = (
        "CREATE VIRTUAL TABLE {name} USING fts5("
        "title, body, tags, symptom, kind UNINDEXED, "
        "content='knowledge', content_rowid='id')"
    )
    _conn.execute(_fts_create.format(name="IF NOT EXISTS knowledge_fts"))
    _fts_cols = {r[1] for r in _conn.execute("PRAGMA table_info(knowledge_fts)")}
    _fts_needs_rebuild = "symptom" not in _fts_cols
    if _fts_needs_rebuild:
        _conn.execute("DROP TABLE knowledge_fts")
        _conn.execute(_fts_create.format(name="knowledge_fts"))
    # Keep the external-content FTS in sync with the knowledge table even when
    # callers bypass the helpers below. Without these triggers a raw DELETE
    # leaves orphan rowids that surface as "missing row N from content table"
    # once queried. Recreated (not IF NOT EXISTS) so they carry the current
    # column set after a shape migration rather than the stale one.
    _conn.execute("DROP TRIGGER IF EXISTS knowledge_ai")
    _conn.execute("DROP TRIGGER IF EXISTS knowledge_ad")
    _conn.execute("DROP TRIGGER IF EXISTS knowledge_au")
    _conn.execute("""
        CREATE TRIGGER knowledge_ai AFTER INSERT ON knowledge BEGIN
            INSERT INTO knowledge_fts(rowid, title, body, tags, symptom, kind)
                VALUES (new.id, new.title, new.body, new.tags, new.symptom, new.kind);
        END
    """)
    _conn.execute("""
        CREATE TRIGGER knowledge_ad AFTER DELETE ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags, symptom, kind)
                VALUES('delete', old.id, old.title, old.body, old.tags, old.symptom, old.kind);
        END
    """)
    _conn.execute("""
        CREATE TRIGGER knowledge_au AFTER UPDATE ON knowledge BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags, symptom, kind)
                VALUES('delete', old.id, old.title, old.body, old.tags, old.symptom, old.kind);
            INSERT INTO knowledge_fts(rowid, title, body, tags, symptom, kind)
                VALUES (new.id, new.title, new.body, new.tags, new.symptom, new.kind);
        END
    """)
    if _fts_needs_rebuild:
        _conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
    # Outcomes: whether relying on an entry, or on a file, worked. A table of
    # its own (never a column on knowledge) so an older owner reading the same
    # store sees nothing new. entry_id is NULL for an outcome about files only;
    # corrected_by points at the entry that fixed a wrong one.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_outcome (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id      INTEGER,
            verdict       TEXT NOT NULL,
            note          TEXT NOT NULL DEFAULT '',
            file_refs     TEXT NOT NULL DEFAULT '',
            corrected_by  INTEGER,
            session_id    TEXT NOT NULL DEFAULT '',
            ts            REAL NOT NULL
        )
    """)
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_outcome_entry "
        "ON knowledge_outcome(entry_id)"
    )
    # How well knowledge_search answered, as daily counts per confidence
    # (high, medium, low, zero). Counts only: no query text, no entry ids.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_recall_stats (
            day      TEXT NOT NULL,
            outcome  TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, outcome)
        )
    """)
    # Who triggered each call (agent, hook, cli, internal) and which repo it
    # served, so usage data separates agent choices from hook traffic and can
    # be aggregated per repo. Rows logged before these columns stay NULL,
    # which reads as "unknown".
    for _col in ("origin", "repo_root"):
        try:
            _conn.execute(f"ALTER TABLE call_log ADD COLUMN {_col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already there
    _conn.commit()
    # Self-heal: if the FTS references rowids that no longer exist, rebuild
    # from the content table. Cheap at open time (runs once per connection).
    # The check is spelled as an INSERT, so sqlite3 opens an implicit
    # transaction for it; end it, or this connection keeps the write lock until
    # its next commit and every other process writing the store (a CLI promote
    # next to a running owner, a hook) times out on "database is locked".
    try:
        _conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES('integrity-check')"
        ).fetchall()
        _conn.commit()
    except sqlite3.DatabaseError:
        try:
            _conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            _conn.commit()
        except sqlite3.DatabaseError:
            pass
    _conns[key] = _conn
    return _conn


# ---------------------------------------------------------------------------
# Session-scoped dedup helpers
# ---------------------------------------------------------------------------


@_locked
def filter_unseen(
    session_id: str,
    entities: list[tuple[str, str]],
    repo_root: str | Path | None = None,
) -> list[tuple[str, str]]:
    """
    Given (kind, key) pairs, return only those NOT yet mentioned in this
    session. Cheap SQL lookup.
    """
    if not session_id or not entities:
        return entities
    conn = _get_conn(repo_root)
    cur = conn.cursor()
    try:
        placeholders = ",".join("(?,?)" for _ in entities)
        flat: list[str] = []
        for kind, key in entities:
            flat.extend((kind, key))
        rows = cur.execute(
            "SELECT entity_kind, entity_key FROM session_mentions "
            f"WHERE session_id = ? AND (entity_kind, entity_key) IN ({placeholders})",
            [session_id, *flat],
        ).fetchall()
        seen = {(r[0], r[1]) for r in rows}
        return [e for e in entities if e not in seen]
    finally:
        cur.close()


@_locked
def record_mentions(
    session_id: str,
    entities: list[tuple[str, str]],
    repo_root: str | Path | None = None,
) -> int:
    """Record (kind, key) pairs as served this session. Returns new count."""
    if not session_id or not entities:
        return 0
    conn = _get_conn(repo_root)
    ts = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO session_mentions(session_id, entity_kind, entity_key, ts) VALUES (?, ?, ?, ?)",
        [(session_id, k, v, ts) for k, v in entities],
    )
    conn.commit()
    return len(entities)


@_locked
def clear_session(session_id: str, repo_root: str | Path | None = None) -> int:
    """Wipe the dedup cache for a specific session (e.g. at session end)."""
    if not session_id:
        return 0
    conn = _get_conn(repo_root)
    cur = conn.execute(
        "DELETE FROM session_mentions WHERE session_id = ?", (session_id,)
    )
    conn.commit()
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Knowledge store, patterns, decisions, gotchas, glossary
# ---------------------------------------------------------------------------


_VALID_KINDS = (
    "pattern",
    "decision",
    "gotcha",
    "style",
    "glossary",
    "note",
    "standing_instruction",
)

# The kinds promotion carries by default: durable, repo-wide learnings. Plain
# notes come too unless the caller opts out; session digests and auto-checkpoint
# markers never promote (they are session state, not knowledge).
_PROMOTABLE_KINDS = (
    "decision",
    "gotcha",
    "pattern",
    "style",
    "glossary",
    "standing_instruction",
)


# Volatile fragments of an error line carry no signal for matching one symptom
# to another: absolute paths, hex / uuid / sha blobs, quoted values, bare
# numbers and timestamps all differ from one run to the next. Normalisation
# replaces them with placeholders (<path>, <hex>, <q>, <n>, <ts>) so a
# diagnostic recorded against one occurrence is recalled by a look-alike, while
# the signal-bearing tokens (an exception name, keywords like "is not
# installed", "database is locked", a SERVICE_DISABLED reason) are kept. Applied
# on both sides: to the recorded symptom (-> symptom_norm) and to a pasted line.
_SYMPTOM_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_SYMPTOM_PATH_RE = re.compile(r"/(?:[\w.\-]+/)+[\w.\-]+")
_SYMPTOM_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SYMPTOM_HEX0X_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_SYMPTOM_HEX_RE = re.compile(r"\b[0-9a-fA-F]{7,}\b")
# A bare 10- or 13-digit integer is an epoch timestamp (seconds / milliseconds),
# gommed to <ts> before the generic number pass so it does not read as <n>.
_SYMPTOM_EPOCH_RE = re.compile(r"\b\d{13}\b|\b\d{10}\b")
_SYMPTOM_NUM_RE = re.compile(r"\b\d+\b")
# A quoted run (single, double, backtick or guillemets) usually wraps a volatile
# value (a module name, a filename, an id), so it collapses to <q> -- unless its
# content is itself a signal to keep: an ALL_CAPS reason code (SERVICE_DISABLED)
# or a known diagnostic phrase. Then the token survives, the quotes dropped.
_SYMPTOM_QUOTE_RE = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`|«[^»]*»")
_SYMPTOM_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_SYMPTOM_KEEP_QUOTED = (
    "is not installed",
    "database is locked",
    "permission denied",
    "command not found",
    "no such file or directory",
    "connection refused",
    "already exists",
    "service_disabled",
)


def _symptom_dequote(match: re.Match) -> str:
    inner = match.group(0)[1:-1].strip()
    low = inner.lower()
    if _SYMPTOM_CONST_RE.match(inner) or any(k in low for k in _SYMPTOM_KEEP_QUOTED):
        return inner
    return "<q>"


def normalize_symptom(text: str) -> str:
    """Return a stable signature for an error line: quoted values, timestamps,
    paths, hex / uuid blobs and bare numbers replaced by placeholders (<q>,
    <ts>, <path>, <hex>, <n>) and runs of whitespace collapsed. Signal-bearing
    tokens (exception names, keywords, ALL_CAPS reason codes) are kept, even
    inside quotes. "" for empty input."""
    if not text:
        return ""
    # Quotes first, on the raw line, so a quoted path or number collapses whole.
    out = _SYMPTOM_QUOTE_RE.sub(_symptom_dequote, text)
    out = _SYMPTOM_TS_RE.sub("<ts>", out)
    out = _SYMPTOM_PATH_RE.sub("<path>", out)
    out = _SYMPTOM_UUID_RE.sub("<hex>", out)
    out = _SYMPTOM_HEX0X_RE.sub("<hex>", out)
    # Only a run of hex digits that holds a letter is hex; an all-digit run is a
    # number, left for the epoch / number passes so it reads as <ts> or <n>.
    out = _SYMPTOM_HEX_RE.sub(
        lambda m: "<hex>" if re.search(r"[a-fA-F]", m.group()) else m.group(), out
    )
    out = _SYMPTOM_EPOCH_RE.sub("<ts>", out)
    out = _SYMPTOM_NUM_RE.sub("<n>", out)
    return " ".join(out.split())


def git_user_name(repo_root: str | Path | None = None) -> str:
    """The name git would stamp on a commit here (`git config user.name`), or ""
    when unset or git is unavailable. Fails closed, never raises."""
    import subprocess

    root = _cache_key(repo_root)
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def repo_slug(repo_root: str | Path | None = None) -> str:
    """A short name for the current repo: the origin remote's repository name
    when set (`.../altikva/cgh.git` -> `cgh`), else the working tree's directory
    name. Fails closed to the directory name, never raises."""
    import subprocess

    root = _cache_key(repo_root)
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            name = proc.stdout.strip().rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(root).name


def _normalize_remote_url(url: str) -> str:
    """A git remote URL reduced to a stable ``host/org/repo`` identity, with the
    scheme, any user info, a port and a trailing ``.git`` stripped. Handles the
    scp-like ``git@host:org/repo`` form too. Returns "" for empty input."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.endswith(".git"):
        u = u[:-4]
    # scp-like syntax has no scheme: [user@]host:path (the colon is not a port).
    if "://" not in u and ":" in u.split("/", 1)[0] and "@" in u.split("/", 1)[0]:
        userhost, _, path = u.partition(":")
        host = userhost.split("@", 1)[-1]
        return f"{host}/{path}".strip("/")
    if "://" in u:
        u = u.split("://", 1)[1]
    if "@" in u.split("/", 1)[0]:
        u = u.split("@", 1)[1]
    host, _, path = u.partition("/")
    host = host.split(":", 1)[0]  # drop a port
    return f"{host}/{path}".strip("/") if path else host


def _persisted_repo_uuid(repo_root: str | Path | None = None) -> str:
    """A per-repo UUID persisted in the state dir (``.cgh/owner_id``), generated
    once and reused. The fallback owner identity when a repo has no git remote;
    stable and unique where a directory name is not. Fails closed, never
    raises, returning a fresh UUID even if it cannot be written."""
    import uuid

    path = Path(_cache_key(repo_root)) / _DB_DIR / "owner_id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


def repo_owner_id(repo_root: str | Path | None = None) -> str:
    """A collision-resistant owner identity for a repo: its normalised git
    remote (``host/org/repo``) when one exists, else a UUID persisted in the
    state dir. Used server-side as the sole authority for a shared-store
    supersede, so two repos that merely share a directory name never pass for
    each other. Fails closed, never raises."""
    import subprocess

    root = _cache_key(repo_root)
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            norm = _normalize_remote_url(proc.stdout)
            if norm:
                return norm
    except (OSError, subprocess.SubprocessError):
        pass
    return _persisted_repo_uuid(root)


@_locked
def knowledge_record(
    title: str,
    body: str,
    kind: str = "note",
    tags: list[str] | str = "",
    file_refs: list[str] | str = "",
    session_id: str = "",
    repo_root: str | Path | None = None,
    supersedes: int = 0,
    scope: str = "repo",
    source_branch: str = "",
    source_commit: str = "",
    source_pr: str = "",
    source_session: str = "",
    origin_ts: float | None = None,
    promoted_at: float | None = None,
    source_worktree: str = "",
    origin_id: int | None = None,
    namespace: str = "repo",
    origin_repo: str = "",
    ticket: str = "",
    author: str = "",
    symptom: str = "",
    contradicts: int = 0,
) -> int:
    """
    Persist a distilled knowledge entry. Returns the row id.

    kind ∈ {pattern, decision, gotcha, style, glossary, note}.
    tags can be a list or a comma/space-separated string.
    file_refs is similar, canonical paths the entry refers to.
    scope is 'repo' (default, promotable) or 'branch' (true only on an unmerged
    branch, not promoted). The source_* fields, origin_id and promoted_at carry
    provenance when an entry is promoted from another checkout; origin_ts
    defaults to now for a native entry and preserves the source entry's
    timestamp on promotion.

    namespace is the logical scope the row belongs to ('repo' by default).
    origin_repo, ticket and author record where the learning came from and are
    stored verbatim (the MCP layer fills author and origin_repo in when a caller
    leaves them blank). symptom is a raw error signature; when given, its
    normalised form is stored in symptom_norm for signature-based recall. All
    five default to the entry's current behaviour, so an existing caller that
    passes none of them is unaffected.

    contradicts is the id of a live entry this note explicitly overturns. With
    tag overlap it is a strong signal: the old entry is auto-superseded (the
    ids returned via the module, see supersede_candidates). Weak matches (tag
    overlap alone) are never retired here; the MCP layer only suggests them.
    """
    if kind not in _VALID_KINDS:
        kind = "note"
    if scope not in ("repo", "branch"):
        scope = "repo"
    if not namespace:
        namespace = "repo"
    ns_stripped = namespace.strip()
    nonlocal_ns = (
        bool(ns_stripped)
        and ns_stripped not in ("repo", "branch")
        and not ns_stripped.startswith("ticket:")
    )
    # A shared namespace must pass the strict name allowlist before it can
    # become a path segment, and must not carry a durable-authority kind: a
    # standing_instruction or glossary entry read by default across every
    # subscribed repo would be cross-repo authority injection, so those kinds
    # stay in the repo store.
    if nonlocal_ns:
        if not valid_shared_namespace(ns_stripped):
            raise ValueError(f"invalid shared namespace name: {namespace!r}")
        if kind in ("standing_instruction", "glossary"):
            raise ValueError(
                f"kind {kind!r} is not allowed in a shared namespace "
                f"({ns_stripped!r}); it stays in the repo store"
            )
    if isinstance(tags, list):
        tags = ",".join(t.strip() for t in tags if t and t.strip())
    if isinstance(file_refs, list):
        file_refs = ",".join(f.strip() for f in file_refs if f and f.strip())
    symptom = symptom or ""
    symptom_norm = normalize_symptom(symptom)
    # A shared namespace routes the write to its own store under the global
    # dir, created on first write; everything else stays in the repo store.
    shared = is_shared_namespace(namespace)
    conn = (
        _get_conn_at(shared_store_path(namespace)) if shared else _get_conn(repo_root)
    )
    # Owner authority is derived server-side and stored only for a shared write;
    # the caller-supplied origin_repo is provenance, never authority.
    owner = repo_owner_id(repo_root) if shared else None
    now = time.time()
    cur = conn.execute(
        "INSERT INTO knowledge(session_id, title, body, tags, kind, file_refs, ts, "
        "scope, source_branch, source_commit, source_pr, source_session, origin_ts, "
        "promoted_at, source_worktree, origin_id, namespace, origin_repo, ticket, "
        "author, symptom, symptom_norm, owner) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            title or "",
            body or "",
            tags or "",
            kind,
            file_refs or "",
            now,
            scope,
            source_branch or "",
            source_commit or "",
            source_pr or "",
            source_session or "",
            origin_ts if origin_ts is not None else now,
            promoted_at,
            source_worktree or None,
            origin_id,
            namespace,
            origin_repo or None,
            ticket or None,
            author or None,
            symptom or None,
            symptom_norm or None,
            owner,
        ),
    )
    row_id = cur.lastrowid
    # FTS mirror is now maintained by the AFTER INSERT trigger.
    if supersedes and row_id:
        allowed = True
        if shared:
            # In a shared store a write may only retire a row of the SAME
            # namespace that this repo owns, so one repo's session cannot
            # overwrite another's shared note. Authority is the SERVER-derived
            # owner identity of the calling repo (collision-resistant: git
            # remote, else a persisted UUID), never the caller-supplied
            # origin_repo. A target with no owner (NULL, or a row written before
            # the column existed) is owned by nobody and denied.
            tgt = conn.execute(
                "SELECT owner, namespace FROM knowledge WHERE id = ?",
                (int(supersedes),),
            ).fetchone()
            allowed = (
                bool(tgt)
                and tgt[1] == namespace
                and bool(tgt[0])
                and bool(owner)
                and tgt[0] == owner
            )
        if allowed:
            conn.execute(
                "UPDATE knowledge SET superseded_by = ? WHERE id = ?",
                (int(row_id), int(supersedes)),
            )
    # Auto-retire the live notes this one strongly contradicts: same kind,
    # tag overlap, and either an explicit contradicts=<id> or a corrected
    # outcome already on the old note. Weak matches (tag overlap only) are
    # left live; the MCP layer surfaces them as a suggestion instead. Only in
    # the repo store: the candidate scan reads the repo store, and a shared
    # write must never auto-retire another repo's rows (it cannot verify
    # ownership), so a shared namespace skips this entirely.
    if row_id and not shared:
        for cand in supersede_candidates(
            repo_root,
            kind=kind,
            tags=tags,
            title=title or "",
            body=body or "",
            contradicts=contradicts,
            exclude_id=int(row_id),
        )["strong"]:
            if cand["id"] == int(supersedes or 0):
                continue
            conn.execute(
                "UPDATE knowledge SET superseded_by = ? "
                "WHERE id = ? AND superseded_by IS NULL",
                (int(row_id), cand["id"]),
            )
    conn.commit()
    return int(row_id or 0)


# Tags that mark session state rather than knowledge: digests, checkpoints
# (manual, compaction and auto) and IBM Bob's auto digests all carry one.
_SESSION_STATE_TAGS = frozenset({"session-digest", "auto-checkpoint", "auto-digest"})


# Auto-supersede detector. A new note can retire a live one it overturns, but
# only on a strong, conservative signal (never by tag overlap alone).
# Minimum Jaccard overlap of two notes' tag sets to read as the same subject.
_SUPERSEDE_MIN_JACCARD = 0.6
# Negation / reversal cues that mark a note as correcting an earlier one. A
# soft signal on its own: it only raises a suggestion, never an auto-supersede.
_CONTRADICTION_CUES = (
    "ne marche pas",
    "ne marche plus",
    "ne fonctionne pas",
    "ne fonctionne plus",
    "en fait",
    "faux",
    "contrairement à",
    "contrairement a",
    "doesn't work",
    "does not work",
    "no longer",
    "actually",
    "contrary to",
    "turns out",
    "was wrong",
)


def _tag_set(tags: list[str] | str) -> set[str]:
    """Lower-cased, de-duplicated tag tokens from a list or a comma/space
    string."""
    items = (
        tags if isinstance(tags, list) else str(tags or "").replace(" ", ",").split(",")
    )
    return {t.strip().lower() for t in items if t and t.strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_contradiction_cue(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in _CONTRADICTION_CUES)


@_locked
def supersede_candidates(
    repo_root: str | Path | None = None,
    *,
    kind: str,
    tags: list[str] | str = "",
    title: str = "",
    body: str = "",
    contradicts: int = 0,
    exclude_id: int = 0,
) -> dict:
    """Live notes a new entry looks set to replace, split into strong and weak.

    A candidate shares the new note's ``kind`` and overlaps its tags
    (Jaccard >= _SUPERSEDE_MIN_JACCARD), or is the explicit ``contradicts``
    target. It is *strong* when the tag overlap holds **and** a hard signal
    names it: an explicit ``contradicts=<id>`` or a ``corrected`` outcome
    already on it. Those are what ``knowledge_record`` auto-supersedes. Tag
    overlap alone, an explicit target below the overlap bar, or a negation cue
    in the new text, is *weak*: a suggestion only.

    Returns ``{"strong": [...], "weak": [...]}``; each item is
    ``{id, title, jaccard, reason}``. Read-only, never mutates.
    """
    new_tags = _tag_set(tags)
    if not new_tags and not contradicts:
        return {"strong": [], "weak": []}
    conn = _get_conn(repo_root)
    cue = _has_contradiction_cue(f"{title}\n{body}")
    corrected_ids = {
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT entry_id FROM knowledge_outcome "
            "WHERE verdict = 'corrected' AND entry_id IS NOT NULL"
        ).fetchall()
    }
    strong: list[dict] = []
    weak: list[dict] = []
    for row_id, row_title, row_tags in conn.execute(
        "SELECT id, title, tags FROM knowledge "
        "WHERE kind = ? AND superseded_by IS NULL",
        (kind,),
    ).fetchall():
        row_id = int(row_id)
        if exclude_id and row_id == exclude_id:
            continue
        if _tag_set(row_tags) & _SESSION_STATE_TAGS:
            continue
        jac = _jaccard(new_tags, _tag_set(row_tags))
        explicit = bool(contradicts) and contradicts == row_id
        corrected = row_id in corrected_ids
        tag_match = jac >= _SUPERSEDE_MIN_JACCARD
        if not tag_match and not explicit:
            continue
        item = {"id": row_id, "title": row_title or "", "jaccard": round(jac, 3)}
        if tag_match and (explicit or corrected):
            item["reason"] = "contradicts" if explicit else "corrected"
            strong.append(item)
            continue
        reasons = []
        if explicit:
            reasons.append("contradicts flagged")
        if tag_match:
            reasons.append("tag overlap")
        if cue:
            reasons.append("negation cue")
        item["reason"] = ", ".join(reasons) or "tag overlap"
        weak.append(item)
    return {"strong": strong, "weak": weak}


def _retirable(tid: int, owned: set[int] | None) -> bool:
    """Whether a promote run may supersede target row ``tid``: always in a
    checkout (``owned`` is None), and only rows this repo owns in a shared
    store."""
    return owned is None or tid in owned


def _supersede_chain(src: sqlite3.Connection) -> dict[int, list[int]]:
    """Map each entry id to the ids it replaced, directly or through a chain."""
    direct: dict[int, list[int]] = {}
    for old_id, new_id in src.execute(
        "SELECT id, superseded_by FROM knowledge WHERE superseded_by IS NOT NULL"
    ).fetchall():
        direct.setdefault(int(new_id), []).append(int(old_id))
    chains: dict[int, list[int]] = {}
    for head in direct:
        seen: list[int] = []
        stack = list(direct[head])
        while stack:
            cur = stack.pop()
            if cur in seen or cur == head:
                continue
            seen.append(cur)
            stack.extend(direct.get(cur, []))
        chains[head] = seen
    return chains


@_locked
def promote_knowledge(
    from_root: str | Path,
    to_root: str | Path | None = None,
    *,
    to_namespace: str = "",
    since_ts: float = 0.0,
    kinds: list[str] | None = None,
    only_ids: list[int] | None = None,
    tags_any: list[str] | None = None,
    include_plain_notes: bool = True,
    source_branch: str = "",
    source_commit: str = "",
    source_pr: str = "",
    source_session: str = "",
    source_worktree: str = "",
    origin_repo: str = "",
    dry_run: bool = False,
) -> dict:
    """Copy durable learnings from a repo store into another store, deduplicated
    and with provenance preserved.

    Two targets. ``to_root`` carries them into a second checkout, the case being
    a per-ticket worktree promoted into its main checkout at merge. ``to_namespace``
    carries them instead into a shared store (``~/.cgh/shared/<name>/knowledge.db``)
    read across the fleet by default: this is the native ``knowledge promote``,
    the matured decision moving ticket -> repo -> org. Exactly one is used; a
    shared namespace must pass the shared-store allowlist.

    Carried: live (not superseded), ``scope='repo'`` entries (a NULL scope from
    before the column existed counts as repo) of the wanted kinds. Session
    digests and checkpoints never move. ``kinds`` replaces the default set
    outright; without it the durable kinds move, plus plain notes unless
    ``include_plain_notes`` is False. ``only_ids`` restricts to those source
    entry ids; ``tags_any`` to entries carrying at least one of those tags.
    A shared target refuses ``standing_instruction`` and ``glossary`` (durable
    cross-repo authority stays in the repo store); those entries are reported
    under ``refused_shared_kind`` and never written.

    Per entry, in order:

    - the same kind, title and body anywhere in the target, live or
      superseded: skipped as a duplicate, so a re-run adds nothing and an
      entry the target has since replaced is not resurrected;
    - it replaced older source entries (``supersedes``) whose copies are live
      in the target, matched by origin id or by content: inserted, and those
      copies are superseded by it (a copy is matched by origin id from this
      same worktree and branch, or by content);
    - otherwise: inserted.

    Every inserted row carries provenance (branch, commit, PR, session,
    worktree, origin id, origin timestamp, promoted_at, plus origin_repo, and
    the source entry's own ticket, author and symptom). In a shared store the
    row is stamped with this repo's server-derived owner, and a supersede only
    retires a row this repo already owns there, never another repo's. ``dry_run``
    computes the same outcome and writes nothing. Returns counts plus one line
    per considered entry under ``entries``.
    """
    shared = bool(to_namespace)
    if shared:
        if not is_shared_namespace(to_namespace):
            raise ValueError(
                f"{to_namespace!r} is not a shared namespace; promote targets a "
                f"shared store (org-platform or a named fleet) or a checkout root"
            )
    elif to_root is None:
        raise ValueError("promote_knowledge needs a target: to_root or to_namespace")

    if kinds:
        wanted = set(kinds)
    else:
        wanted = set(_PROMOTABLE_KINDS) | ({"note"} if include_plain_notes else set())
    only = {int(i) for i in only_ids} if only_ids is not None else None
    tagwanted = (
        {t.strip().lower() for t in tags_any if t and t.strip()}
        if tags_any is not None
        else None
    )
    worktree = source_worktree or str(Path(from_root).resolve())
    origin_repo = origin_repo or repo_slug(from_root)

    src = _get_conn(from_root)
    cols = (
        "id, title, body, kind, tags, file_refs, session_id, ts, scope, "
        "superseded_by, ticket, author, symptom"
    )
    all_rows = src.execute(f"SELECT {cols} FROM knowledge ORDER BY ts ASC").fetchall()
    by_id = {r[0]: r for r in all_rows}
    chains = _supersede_chain(src)

    # Target store: the shared store file for a namespace, else the checkout.
    # A shared write is attributed to this repo's collision-resistant owner.
    owner = repo_owner_id(from_root) if shared else None
    tgt = (
        _get_conn_at(shared_store_path(to_namespace)) if shared else _get_conn(to_root)
    )
    # Every target row, superseded ones included: a re-run must not resurrect
    # an entry the target has since replaced. owned_targets is the subset this
    # repo may retire in a shared store (None = a checkout, where all apply).
    content_rows: dict[tuple, list[tuple[int, bool]]] = {}
    origin_rows: dict[int, list[tuple[int, bool]]] = {}
    owned_targets: set[int] | None = set() if shared else None
    for tid, kind, title, body, sup, oid, wt, br, row_owner, row_ns in tgt.execute(
        "SELECT id, kind, title, body, superseded_by, origin_id, source_worktree, "
        "source_branch, owner, namespace FROM knowledge"
    ).fetchall():
        content_rows.setdefault((kind, title, body), []).append((tid, sup is None))
        own = owned_targets is None or (
            bool(owner) and row_owner == owner and row_ns == to_namespace
        )
        if own and owned_targets is not None:
            owned_targets.add(int(tid))
        if oid is not None and wt == worktree and (br or "") == source_branch and own:
            origin_rows.setdefault(int(oid), []).append((tid, sup is None))

    copied = superseded = skipped = considered = refused = 0
    entries: list[dict] = []
    next_fake_id = -1  # dry-run stand-in ids, never written
    for row in all_rows:
        (sid, title, body, kind, tags, file_refs, sess, ts, scope, sup,
         ticket, author, symptom) = row  # fmt: skip
        if sup is not None:
            continue
        tagset = {t.strip() for t in (tags or "").split(",") if t.strip()}
        if tagset & _SESSION_STATE_TAGS:
            continue
        if (scope or "repo") != "repo":
            continue
        if kind not in wanted:
            continue
        if only is not None and sid not in only:
            continue
        if tagwanted is not None and not ({t.lower() for t in tagset} & tagwanted):
            continue
        if (ts or 0.0) < since_ts:
            continue
        line = {"source_id": sid, "kind": kind, "title": title}

        # A shared store never takes a durable cross-repo-authority kind.
        if shared and kind in ("standing_instruction", "glossary"):
            refused += 1
            entries.append({**line, "action": "refused_shared_kind"})
            continue

        considered += 1
        content = (kind, title, body)

        if content in content_rows:
            skipped += 1
            entries.append({**line, "action": "duplicate"})
            continue

        # Live target copies of the entries this one replaced (only rows this
        # repo owns, in a shared store).
        replaces: list[int] = []
        for old_id in chains.get(sid, []):
            for tid, live in origin_rows.get(old_id, []):
                if live and tid not in replaces and _retirable(tid, owned_targets):
                    replaces.append(tid)
            old = by_id.get(old_id)
            if old is not None:
                for tid, live in content_rows.get((old[3], old[1], old[2]), []):
                    if live and tid not in replaces and _retirable(tid, owned_targets):
                        replaces.append(tid)

        if dry_run:
            new_id = next_fake_id
            next_fake_id -= 1
        else:
            new_id = knowledge_record(
                title,
                body,
                kind=kind,
                tags=tags or "",
                file_refs=file_refs or "",
                repo_root=from_root if shared else to_root,
                scope="repo",
                source_branch=source_branch,
                source_commit=source_commit,
                source_pr=source_pr,
                source_session=source_session or sess or "",
                origin_ts=ts,
                promoted_at=time.time(),
                source_worktree=worktree,
                origin_id=sid,
                namespace=to_namespace if shared else "repo",
                origin_repo=origin_repo,
                ticket=ticket or "",
                author=author or "",
                symptom=symptom or "",
            )
            if replaces:
                tgt.executemany(
                    "UPDATE knowledge SET superseded_by = ? "
                    "WHERE id = ? AND superseded_by IS NULL",
                    [(new_id, tid) for tid in replaces],
                )
                tgt.commit()
        # Keep the in-memory view current so later rows in this run see it.
        if replaces:
            gone = set(replaces)
            for view in (*content_rows.values(), *origin_rows.values()):
                view[:] = [(rid, live and rid not in gone) for rid, live in view]
        content_rows.setdefault(content, []).append((new_id, True))
        origin_rows.setdefault(sid, []).append((new_id, True))
        # A row this repo just wrote to the shared store is ours to retire too.
        if owned_targets is not None and new_id > 0:
            owned_targets.add(new_id)

        if replaces:
            superseded += 1
            entries.append(
                {**line, "action": "superseded", "target_id": new_id,
                 "supersedes": replaces}
            )  # fmt: skip
        else:
            copied += 1
            entries.append({**line, "action": "copied", "target_id": new_id})

    if dry_run:
        for e in entries:
            e.pop("target_id", None)
    result = {
        "promoted": copied + superseded,
        "copied": copied,
        "superseded": superseded,
        "skipped_duplicate": skipped,
        "considered": considered,
        "dry_run": dry_run,
        "entries": entries,
    }
    if shared:
        result["target_namespace"] = to_namespace
        result["refused_shared_kind"] = refused
    return result


def knowledge_export(repo_root: str | Path | None = None) -> list[dict]:
    """Every knowledge row with every column, superseded ones and session
    digests included, oldest first. Used to archive a store before its
    worktree is removed."""
    with _LOG_LOCK:
        conn = _get_conn(repo_root)
        cur = conn.execute("SELECT * FROM knowledge ORDER BY id ASC")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@_locked
def knowledge_search(
    query: str,
    kind: str | None = None,
    limit: int = 10,
    repo_root: str | Path | None = None,
    rank_outcomes: bool = True,
    recall: bool = False,
    namespace: str | None = None,
) -> list[dict]:
    """BM25 search over knowledge entries. Graceful LIKE fallback.

    With rank_outcomes (the default) and outcomes on record, a wider candidate
    set is fetched, each relevance score is multiplied by the entry's outcome
    factor (useful up, dead ends and corrected entries down), and every rated
    hit carries an ``outcome`` summary.

    With recall, a free-text query also matches entries worded differently
    (stems, folded accents and case, French and English synonyms, close
    spellings as a last resort), and every hit carries a ``confidence`` of
    high, medium or low. When only weak matches exist, at most three come
    back.
    """
    conn = _get_conn(repo_root)
    search = _knowledge_recall if recall else _knowledge_search
    use_outcomes = rank_outcomes and _has_outcomes(conn)
    # Recency is a ranking factor, so rank_outcomes=False (an exact resolve of a
    # query to one entry) opts out of it too, staying on pure relevance.
    recency_hl = _recency_half_life_days(repo_root) if rank_outcomes else 0.0
    # Default path: no outcomes to rank by and age decay off -> plain relevance,
    # byte-for-byte the behaviour before age decay existed.
    if not use_outcomes and recency_hl <= 0:
        return _cap_weak(
            search(conn, query, kind, limit, namespace=namespace), limit, recall
        )
    from cgh.state.knowledge_outcomes import rank_factor, recency_factor

    now = time.time()
    entries: dict = {}
    if use_outcomes:
        entries, _files = _outcome_scores(conn, now, _half_life_days(repo_root))
    # Room for a well-rated or fresher entry just under the cut to move up.
    raw: list[float] = []
    hits = search(
        conn, query, kind, max(limit * 3, limit + 10), raw, namespace=namespace
    )
    # Rank on the unrounded relevance: on a small store BM25 scores are tiny
    # and round to zero, which would erase the outcome and recency factors.
    adjusted: list[float] = []
    for h, r in zip(hits, raw, strict=True):
        score = entries.get(h["id"])
        factor = rank_factor(score)
        if recency_hl > 0:
            # origin_ts is preserved across promotion and equals ts for a
            # native entry; it rides along only for promoted rows, so fall
            # back to ts. standing_instruction, glossary and entries still
            # rated useful/preferred are shielded from the age decay.
            origin_ts = (
                (h.get("provenance") or {}).get("origin_ts") or h.get("ts") or now
            )
            factor *= recency_factor(
                now - origin_ts,
                recency_hl,
                kind=h.get("kind"),
                status=score.status if score else None,
            )
        adjusted.append(r * factor)
    order = sorted(range(len(hits)), key=lambda i: -adjusted[i])  # stable on ties
    hits = [hits[i] | {"score": round(adjusted[i], 4)} for i in order[:limit]]
    if use_outcomes:
        _attach_outcomes(hits, entries)
    return _cap_weak(hits, limit, recall)


# Most weak (low confidence) hits a recall search returns when it found
# nothing better: enough to show what is close, few enough to stay cheap.
_WEAK_HITS = 3


def _cap_weak(hits: list[dict], limit: int, recall: bool) -> list[dict]:
    """Weak hits only fill an answer that has nothing better: dropped next
    to a high or medium hit, at most _WEAK_HITS when they are all there is."""
    if not (recall and hits):
        return hits
    strong = [h for h in hits if h.get("confidence") != "low"]
    return strong or hits[: min(limit, _WEAK_HITS)]


def _fts_rows(
    conn: sqlite3.Connection,
    expr: str,
    kind: str | None,
    limit: int,
    namespace: str | None = None,
) -> list[dict]:
    """Live entries matching an FTS5 expression, best BM25 first."""
    sql = (
        f"SELECT {_k_cols('k.')}, rank AS score "
        "FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
        "WHERE knowledge_fts MATCH ? AND k.superseded_by IS NULL "
    )
    params: list = [expr]
    if kind:
        sql += "AND k.kind = ? "
        params.append(kind)
    if namespace:
        sql += "AND k.namespace = ? "
        params.append(namespace)
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_knowledge_row_to_dict(row, score=-row[-1]) for row in rows]


def _store_words(
    conn: sqlite3.Connection, kind: str | None, namespace: str | None = None
) -> set[str]:
    """Every folded word of the live entries, for close-spelling lookups."""
    from cgh.state.knowledge_recall import fold

    sql = "SELECT title, body, tags FROM knowledge WHERE superseded_by IS NULL"
    params: list = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if namespace:
        sql += " AND namespace = ?"
        params.append(namespace)
    out: set[str] = set()
    for title, body, tags in conn.execute(sql, params).fetchall():
        out.update(re.findall(r"[a-z0-9_]+", fold(f"{title} {tags} {body}")))
    return out


def _knowledge_recall(
    conn: sqlite3.Connection,
    query: str,
    kind: str | None,
    limit: int,
    raw_scores: list[float] | None = None,
    namespace: str | None = None,
) -> list[dict]:
    """Free-text search behind knowledge_search(recall=True). Caller holds
    the lock.

    The query as written runs first, so FTS syntax keeps working. Then the
    query's concepts (stems and synonyms) are ORed, and when that finds
    nothing either, close spellings of the query words are tried. Each hit is
    graded by the share of concepts its title, tags and body cover; the
    relevance score orders by that grade first, BM25 inside a grade.
    """
    from cgh.state import knowledge_recall as kr

    raw = raw_scores if raw_scores is not None else []
    raw.clear()
    items = kr.concepts(query)
    pool = max(limit * 3, 30)
    strict = (
        _knowledge_search(conn, query, kind, limit, namespace=namespace)
        if query.strip()
        else []
    )
    strict_ids = {h["id"] for h in strict}
    candidates: dict[int, dict] = {h["id"]: h for h in strict}
    expr = kr.fts_expression(items)
    if expr:
        for h in _fts_rows(conn, expr, kind, pool, namespace=namespace):
            candidates.setdefault(h["id"], h)
    fuzzy = False
    if not candidates and items:
        close = kr.close_words(items, _store_words(conn, kind, namespace=namespace))
        if close:
            fuzzy = True
            expr = " OR ".join(f'"{w}"' for w in close)
            for h in _fts_rows(conn, expr, kind, pool, namespace=namespace):
                candidates.setdefault(h["id"], h)

    graded: list[tuple[int, float, float, dict]] = []
    for h in candidates.values():
        matched, counted = kr.coverage(
            items, f"{h['title']} {' '.join(h['tags'])}", h["body"]
        )
        ratio = matched / counted if counted else 0.0
        conf = "low" if fuzzy else kr.confidence(matched, counted)
        if conf is None:
            if h["id"] not in strict_ids:
                continue
            conf, ratio = "high", 1.0  # matched as written, no word to grade on
        bm25 = max(float(h.get("score") or 0.0), 0.0)
        tier = kr.CONFIDENCE_ORDER.index(conf)
        graded.append((tier, ratio, bm25, h | {"confidence": conf}))
    graded.sort(key=lambda g: (g[0], -g[1], -g[2]))  # stable on ties
    out: list[dict] = []
    for tier, ratio, bm25, h in graded[:limit]:
        # Grade first, then coverage, then BM25: each term stays under 1.
        relevance = (
            (len(kr.CONFIDENCE_ORDER) - tier) + 0.5 * ratio + 0.49 * bm25 / (1.0 + bm25)
        )
        raw.append(relevance)
        out.append(h | {"score": round(relevance, 4)})
    return out


@_locked
def record_recall_outcome(outcome: str, repo_root: str | Path | None = None) -> None:
    """Count one knowledge search by how well it answered: high, medium,
    low or zero. Only the day and the count are stored."""
    if outcome not in ("high", "medium", "low", "zero"):
        return
    conn = _get_conn(repo_root)
    conn.execute(
        "INSERT INTO knowledge_recall_stats (day, outcome, count) VALUES (?, ?, 1) "
        "ON CONFLICT(day, outcome) DO UPDATE SET count = count + 1",
        (time.strftime("%Y-%m-%d"), outcome),
    )
    conn.commit()


def _recall_stats(conn: sqlite3.Connection) -> dict:
    """Totals of knowledge_recall_stats with the zero-hit and weak rates."""
    counts = {"high": 0, "medium": 0, "low": 0, "zero": 0}
    for outcome, n in conn.execute(
        "SELECT outcome, SUM(count) FROM knowledge_recall_stats GROUP BY outcome"
    ).fetchall():
        if outcome in counts:
            counts[outcome] = int(n or 0)
    searches = sum(counts.values())

    def rate(n: int) -> str:
        return f"{n / searches * 100:.1f}%" if searches else "0%"

    return {
        "searches": searches,
        "by_confidence": counts,
        "zero_hit_rate": rate(counts["zero"]),
        "low_confidence_rate": rate(counts["low"]),
    }


def _knowledge_search(
    conn: sqlite3.Connection,
    query: str,
    kind: str | None,
    limit: int,
    raw_scores: list[float] | None = None,
    namespace: str | None = None,
) -> list[dict]:
    """The plain relevance search behind knowledge_search. Caller holds the
    lock. raw_scores, when given, receives each hit's unrounded score."""
    raw = raw_scores if raw_scores is not None else []
    out: list[dict] = []
    try:
        sql = (
            f"SELECT {_k_cols('k.')}, rank AS score "
            "FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH ? AND k.superseded_by IS NULL "
        )
        params: list = [query]
        if kind:
            sql += "AND k.kind = ? "
            params.append(kind)
        if namespace:
            sql += "AND k.namespace = ? "
            params.append(namespace)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        for row in conn.execute(sql, params).fetchall():
            raw.append(-row[-1])
            out.append(_knowledge_row_to_dict(row, score=-row[-1]))
    except sqlite3.OperationalError:
        like = f"%{query}%"
        sql = (
            f"SELECT {_k_cols()} FROM knowledge "
            "WHERE superseded_by IS NULL AND (title LIKE ? OR body LIKE ? OR tags LIKE ?) "
        )
        params = [like, like, like]
        if kind:
            sql += "AND kind = ? "
            params.append(kind)
        if namespace:
            sql += "AND namespace = ? "
            params.append(namespace)
        sql += "ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        raw.clear()  # the FTS attempt may have filled part of it
        for i, row in enumerate(conn.execute(sql, params).fetchall()):
            raw.append(1.0 / (i + 1))
            out.append(_knowledge_row_to_dict(row, score=1.0 / (i + 1)))
    return out


def knowledge_search_ro(
    db_path: Path,
    query: str,
    kind: str | None = None,
    limit: int = 10,
    with_provenance: bool = False,
) -> list[dict]:
    """Read-only knowledge search against an arbitrary knowledge store (a
    federated child's call_log.db, or a shared store's knowledge.db). Fresh
    connection, LIKE-based on purpose: robust against FTS schema drift in the
    store. An empty query matches every live entry, newest first, so the same
    reader lists a store. Superseded entries stay out, like everywhere else.

    With with_provenance, each hit also carries origin_repo, origin_id and
    namespace when the store has those columns, for cross-store de-duplication;
    a store predating them (an old child) silently reads back without."""
    if not db_path.exists():
        return []
    from cgh.core.utils import ro_sqlite_uri

    try:
        conn = sqlite3.connect(ro_sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return []
    like = f"%{query}%"
    base = "id, kind, title, body, tags, file_refs, session_id, ts"
    prov = ", origin_repo, origin_id, namespace"

    def _run(cols: str) -> list:
        sql = (
            f"SELECT {cols} FROM knowledge WHERE superseded_by IS NULL "
            "AND (title LIKE ? OR body LIKE ? OR tags LIKE ?) "
        )
        params: list = [like, like, like]
        if kind:
            sql += "AND kind = ? "
            params.append(kind)
        sql += "ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    has_prov = with_provenance
    try:
        try:
            rows = _run(base + prov) if with_provenance else _run(base)
        except sqlite3.Error:
            # A store without the provenance columns (an old child): retry bare.
            rows = _run(base)
            has_prov = False
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    for i, row in enumerate(rows):
        hit = _knowledge_row_to_dict(row[:8], score=1.0 / (i + 1))
        if has_prov:
            hit["origin_repo"] = row[8]
            hit["origin_id"] = row[9]
            hit["namespace"] = row[10]
        out.append(hit)
    return out


@_locked
def knowledge_get(entry_id: int, repo_root: str | Path | None = None) -> dict | None:
    """One entry by id, whole, superseded or not; None when no such id."""
    conn = _get_conn(repo_root)
    row = conn.execute(
        f"SELECT {_k_cols()} FROM knowledge WHERE id = ?", (int(entry_id),)
    ).fetchone()
    if row is None:
        return None
    out = _knowledge_row_to_dict(row)
    out.pop("score", None)
    if _has_outcomes(conn):
        entries, _files = _outcome_scores(conn, time.time(), _half_life_days(repo_root))
        _attach_outcomes([out], entries)
    return out


@_locked
def standing_rule_entries(repo_root: str | Path | None = None) -> list[dict]:
    """Live standing instructions of the repo store whose body carries a rule
    line (``cgh-rule``), newest first, as ``{id, title, body, session_id, ts}``.

    The only source of rules for check_action: the store of this checkout.
    Shared stores are not opened, a row filed under a shared namespace is
    dropped, and a superseded entry is how a rule is withdrawn, so it is out.
    """
    return _rule_entries(_get_conn(repo_root))


def standing_rule_entries_readonly(
    repo_root: str | Path, busy_timeout: float = 0.05
) -> list[dict] | None:
    """``standing_rule_entries`` for a caller on a deadline that must not
    write: the store file is opened read-only on a connection of its own,
    waiting at most ``busy_timeout`` seconds on a lock, and no directory,
    file or schema is created. None when the repo has no store yet. A locked,
    corrupt or older store raises ``sqlite3.Error``: the caller decides what
    a store it cannot read means.
    """
    db_path = Path(repo_root) / _DB_DIR / _LOG_FILE
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=busy_timeout
    )
    try:
        return _rule_entries(conn)
    finally:
        conn.close()


def _rule_entries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, body, session_id, ts, namespace FROM knowledge "
        "WHERE kind = 'standing_instruction' AND superseded_by IS NULL "
        "AND body LIKE '%cgh-rule%' ORDER BY ts DESC, id DESC"
    ).fetchall()
    return [
        {
            "id": row[0],
            "title": row[1] or "",
            "body": row[2] or "",
            "session_id": row[3] or "",
            "ts": row[4],
        }
        for row in rows
        if not is_shared_namespace(row[5] or "")
    ]


def check_action(
    action: str,
    session_id: str = "",
    repo_root: str | Path | None = None,
    git_timeout: float | None = None,
) -> dict:
    """Check an action against the rule lines of this repo's standing
    instructions. Read-only. The answer shape is the one of the check_action
    MCP tool; see cgh/state/action_rules.py for the matching. ``git_timeout``
    is the budget in seconds of the git call that reads the branch (two
    seconds by default); a caller on a tighter deadline passes its own."""
    from cgh.state import action_rules

    root = Path(repo_root) if repo_root else Path.cwd()
    out = action_rules.evaluate(
        action,
        standing_rule_entries(root),
        session_id=session_id or "",
        cwd=root,
        git_timeout=git_timeout,
    )
    if out["verdict"] == "no_rule" and out["recognised"]:
        words = action_rules.search_words(out["actions"])
        if words:
            try:
                hits = knowledge_search(
                    words,
                    kind="standing_instruction",
                    limit=3,
                    repo_root=root,
                    recall=True,
                )
            except sqlite3.Error:
                hits = []
            out["related"] = [
                {
                    "id": h.get("id"),
                    "title": h.get("title") or "",
                    "confidence": h.get("confidence") or "low",
                }
                for h in hits[:3]
            ]
    return out


@_locked
def knowledge_list(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo_root: str | Path | None = None,
    exclude_tag: str | None = None,
    rank_outcomes: bool = False,
    namespace: str | None = None,
) -> list[dict]:
    """Browse knowledge entries. Filters: kind / tag (substring) / session /
    exclude_tag (substring to omit) / namespace (exact). Pagination: limit +
    offset. Caller can fetch limit+1 to detect has_more.

    Newest first. With rank_outcomes, entries are tiered by outcome first
    (useful, then unrated or contested, then dead ends, then corrected) and
    newest first inside a tier; off by default because internal callers rely
    on plain recency (the latest digest of a session, for one). Rated entries
    carry an ``outcome`` summary either way.
    """
    conn = _get_conn(repo_root)
    entries: dict = {}
    if _has_outcomes(conn):
        entries, _files = _outcome_scores(conn, time.time(), _half_life_days(repo_root))
    sql = f"SELECT {_k_cols()} FROM knowledge"
    where: list[str] = ["superseded_by IS NULL"]
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if exclude_tag:
        where.append("tags NOT LIKE ?")
        params.append(f"%{exclude_tag}%")
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if namespace:
        where.append("namespace = ?")
        params.append(namespace)
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = "ts DESC"
    if rank_outcomes and entries:
        from cgh.state.knowledge_outcomes import list_bucket

        tiers: dict[int, list[int]] = {}
        for entry_id, score in entries.items():
            bucket = list_bucket(score)
            if bucket != 1:
                tiers.setdefault(bucket, []).append(entry_id)
        if tiers:
            cases = []
            for bucket in sorted(tiers):
                ids = tiers[bucket]
                cases.append(f"WHEN id IN ({','.join('?' for _ in ids)}) THEN {bucket}")
                params.extend(ids)
            order = f"CASE {' '.join(cases)} ELSE 1 END, ts DESC"
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    hits = [
        _knowledge_row_to_dict(row, score=row[7])
        for row in conn.execute(sql, params).fetchall()
    ]
    _attach_outcomes(hits, entries)
    return hits


@_locked
def knowledge_count(
    kind: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
    repo_root: str | Path | None = None,
    namespace: str | None = None,
) -> int:
    """Total matching entries, use alongside knowledge_list for pagination."""
    conn = _get_conn(repo_root)
    sql = "SELECT count(*) FROM knowledge"
    # Match knowledge_list, which excludes superseded rows. Without this the
    # count exceeds what list can return, and the pagination it feeds (has_more
    # / next_offset) points past the end.
    where: list[str] = ["superseded_by IS NULL"]
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if namespace:
        where.append("namespace = ?")
        params.append(namespace)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(conn.execute(sql, params).fetchone()[0])


@_locked
def knowledge_terms(
    min_count: int = 1,
    repo_root: str | Path | None = None,
) -> list[tuple[str, int]]:
    """
    Return the glossary, every tag with its occurrence count, sorted by
    frequency. Acts as the "dict" of knowledge.
    """
    conn = _get_conn(repo_root)
    counts: dict[str, int] = {}
    for (tags_csv,) in conn.execute("SELECT tags FROM knowledge WHERE tags <> ''"):
        for t in tags_csv.split(","):
            t = t.strip().lower()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
    return sorted(
        ((t, n) for t, n in counts.items() if n >= min_count),
        key=lambda kv: (-kv[1], kv[0]),
    )


@_locked
def knowledge_forget(
    entry_id: int,
    repo_root: str | Path | None = None,
) -> bool:
    """Delete a single knowledge entry + its FTS row."""
    conn = _get_conn(repo_root)
    existed = conn.execute(
        "SELECT 1 FROM knowledge WHERE id = ?", (entry_id,)
    ).fetchone()
    if not existed:
        return False
    # FTS sync is handled by the AFTER DELETE trigger.
    conn.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
    conn.execute("DELETE FROM knowledge_outcome WHERE entry_id = ?", (entry_id,))
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Knowledge outcomes: did relying on an entry, or a file, work?
# ---------------------------------------------------------------------------


def _split_refs(refs: list[str] | str) -> list[str]:
    if isinstance(refs, str):
        refs = refs.split(",")
    out: list[str] = []
    for r in refs:
        r = (r or "").strip()
        if r and r not in out:
            out.append(r)
    return out


def _half_life_days(repo_root: str | Path | None) -> float:
    """[cgh] knowledge_half_life_days, or the default when the config
    cannot be read (scoring must never fail on a config problem)."""
    from cgh.state.knowledge_outcomes import DEFAULT_HALF_LIFE_DAYS

    try:
        from cgh.core.config import load_config

        return float(load_config(repo_root).knowledge_half_life_days)
    except Exception:
        return DEFAULT_HALF_LIFE_DAYS


def _recency_half_life_days(repo_root: str | Path | None) -> float:
    """[cgh] knowledge_recency_half_life_days (a note's own age decay), or
    0 (off) when the config cannot be read. Distinct from the outcome
    half-life; scoring must never fail on a config problem."""
    from cgh.state.knowledge_outcomes import DEFAULT_RECENCY_HALF_LIFE_DAYS

    try:
        from cgh.core.config import load_config

        return float(load_config(repo_root).knowledge_recency_half_life_days)
    except Exception:
        return DEFAULT_RECENCY_HALF_LIFE_DAYS


@_locked
def knowledge_outcome_record(
    verdict: str,
    entry_id: int | None = None,
    file_refs: list[str] | str = "",
    note: str = "",
    corrected_by: int | None = None,
    session_id: str = "",
    repo_root: str | Path | None = None,
) -> int:
    """Store one outcome and return its id.

    verdict is one of useful, dead_end, corrected. The target is an entry, a
    set of files, or both; at least one is required. corrected needs an entry
    and accepts the id of the entry that fixed it. Raises ValueError on a bad
    verdict, a missing target, or an id that does not exist.
    """
    from cgh.state.knowledge_outcomes import VERDICTS

    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    refs = _split_refs(file_refs)
    if not entry_id and not refs:
        raise ValueError("an outcome needs an entry id, a query or file_refs")
    if corrected_by and verdict != "corrected":
        raise ValueError("corrected_by only goes with the corrected verdict")
    if verdict == "corrected" and not entry_id:
        raise ValueError("the corrected verdict needs the entry that was wrong")
    conn = _get_conn(repo_root)
    for label, ident in (("entry", entry_id), ("corrected_by entry", corrected_by)):
        if (
            ident
            and not conn.execute(
                "SELECT 1 FROM knowledge WHERE id = ?", (int(ident),)
            ).fetchone()
        ):
            raise ValueError(f"no knowledge {label} with id {ident}")
    cur = conn.execute(
        "INSERT INTO knowledge_outcome(entry_id, verdict, note, file_refs, "
        "corrected_by, session_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            int(entry_id) if entry_id else None,
            verdict,
            note or "",
            ",".join(refs),
            int(corrected_by) if corrected_by else None,
            session_id or "",
            time.time(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def _outcome_scores(
    conn: sqlite3.Connection,
    now: float,
    half_life_days: float,
) -> tuple[dict, dict]:
    """Per-entry and per-file OutcomeScore maps. Caller holds the lock."""
    from cgh.state.knowledge_outcomes import score_outcomes

    by_entry: dict[int, list] = {}
    by_file: dict[str, list] = {}
    for entry_id, verdict, note, refs, corrected_by, ts in conn.execute(
        "SELECT entry_id, verdict, note, file_refs, corrected_by, ts "
        "FROM knowledge_outcome"
    ).fetchall():
        row = (verdict, ts, note, corrected_by)
        if entry_id is not None:
            by_entry.setdefault(int(entry_id), []).append(row)
        for ref in _split_refs(refs or ""):
            # A correction is about an entry; for its files it reads as a
            # plain dead end.
            by_file.setdefault(ref, []).append(
                ("dead_end" if verdict == "corrected" else verdict, ts, note, None)
            )
    entries = {k: score_outcomes(v, now, half_life_days) for k, v in by_entry.items()}
    files = {k: score_outcomes(v, now, half_life_days) for k, v in by_file.items()}
    return entries, files


def _has_outcomes(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM knowledge_outcome LIMIT 1").fetchone() is not None
    )


@_locked
def knowledge_outcome_scores(
    repo_root: str | Path | None = None,
    now: float | None = None,
    half_life_days: float | None = None,
) -> tuple[dict, dict]:
    """(entries, files): OutcomeScore per entry id and per file path."""
    conn = _get_conn(repo_root)
    return _outcome_scores(
        conn,
        time.time() if now is None else now,
        _half_life_days(repo_root) if half_life_days is None else half_life_days,
    )


@_locked
def knowledge_dead_ends(
    limit: int = 5,
    repo_root: str | Path | None = None,
    now: float | None = None,
    half_life_days: float | None = None,
) -> list[dict]:
    """Known dead ends, strongest and most recent first: live entries whose
    outcomes say dead_end or corrected, and files whose outcomes say
    dead_end. Faded verdicts are left out."""
    conn = _get_conn(repo_root)
    if not _has_outcomes(conn):
        return []
    entries, files = _outcome_scores(
        conn,
        time.time() if now is None else now,
        _half_life_days(repo_root) if half_life_days is None else half_life_days,
    )
    items: list[tuple[float, float, dict]] = []
    bad = [i for i, s in entries.items() if s.status in ("dead_end", "corrected")]
    if bad:
        placeholders = ",".join("?" for _ in bad)
        titles = dict(
            conn.execute(
                f"SELECT id, title FROM knowledge WHERE id IN ({placeholders}) "
                "AND superseded_by IS NULL",
                bad,
            ).fetchall()
        )
        for entry_id in bad:
            if entry_id not in titles:
                continue  # forgotten or superseded
            s = entries[entry_id]
            item = {"entry_id": entry_id, "title": titles[entry_id], **s.summary()}
            items.append((s.negative_weight, s.last_ts, item))
    for ref, s in files.items():
        if s.status == "dead_end":
            items.append((s.negative_weight, s.last_ts, {"file": ref, **s.summary()}))
    items.sort(key=lambda t: (-t[0], -t[1]))
    return [item for _, _, item in items[: max(0, limit)]]


def _attach_outcomes(hits: list[dict], entries: dict) -> None:
    for hit in hits:
        s = entries.get(hit["id"])
        if s is not None:
            hit["outcome"] = s.summary()


# ---------------------------------------------------------------------------
# Symptom (error-signature) search
# ---------------------------------------------------------------------------

# Columns a symptom hit carries, enough to diagnose: who recorded it, where and
# when, the ticket, and the raw / normalised signature.
_K_SYM_COLS = (
    "id",
    "kind",
    "title",
    "body",
    "tags",
    "session_id",
    "ts",
    "namespace",
    "origin_repo",
    "ticket",
    "author",
    "origin_ts",
    "symptom",
    "symptom_norm",
)
# Placeholder residue dropped from the token set: every normalised line carries
# these, so matching on them would make unrelated errors look alike.
_SYMPTOM_STOP = frozenset({"ts", "path", "hex", "n", "q"})
# Minimum token overlap (Jaccard) for a recorded signature to count as a
# look-alike of the pasted line; an exact normalised match always scores 1.
_SYMPTOM_MIN_OVERLAP = 0.34


def _symptom_tokens(norm: str) -> frozenset[str]:
    """Signal tokens of a normalised signature, placeholder residue removed."""
    return frozenset(
        t
        for t in re.findall(r"[a-z0-9_]+", (norm or "").lower())
        if len(t) >= 2 and t not in _SYMPTOM_STOP
    )


def _sym_row_to_dict(row) -> dict:
    d = dict(zip(_K_SYM_COLS, row, strict=True))
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    return d


def _symptom_hits(
    conn: sqlite3.Connection,
    symptom_line: str,
    limit: int,
    namespace: str | None = None,
    min_overlap: float = _SYMPTOM_MIN_OVERLAP,
) -> list[dict]:
    """Live entries whose recorded signature (or, failing that, body) resembles
    ``symptom_line``. A normalised signature matched by overlap scores in
    [min_overlap, 1]; a plain body / title mention found via FTS scores lower,
    so a signature match always ranks first. Caller holds the lock. Tolerant of
    a store that predates the symptom columns (returns nothing from it)."""
    norm = normalize_symptom(symptom_line)
    want = _symptom_tokens(norm)
    sel = ", ".join(_K_SYM_COLS)
    out: dict[int, dict] = {}
    sql = (
        f"SELECT {sel} FROM knowledge "
        "WHERE superseded_by IS NULL AND symptom_norm IS NOT NULL "
    )
    params: list = []
    if namespace:
        sql += "AND namespace = ? "
        params.append(namespace)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # a store without the symptom columns has nothing to match
    for row in rows:
        d = _sym_row_to_dict(row)
        other = _symptom_tokens(d.get("symptom_norm") or "")
        if norm and d.get("symptom_norm") == norm:
            score = 1.0
        elif want and other:
            score = len(want & other) / len(want | other)
        else:
            score = 0.0
        if score >= min_overlap:
            d["match"] = "symptom"
            d["score"] = round(score, 4)
            out[d["id"]] = d
    # A diagnostic can live in a note's body without a stored signature: find it
    # by FTS on the line's salient words, scored by word coverage and capped
    # below a real signature match.
    if want:
        expr = " OR ".join(f'"{t}"' for t in sorted(want))
        fsel = ", ".join("k." + c for c in _K_SYM_COLS)
        fsql = (
            f"SELECT {fsel} FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH ? AND k.superseded_by IS NULL "
        )
        fparams: list = [expr]
        if namespace:
            fsql += "AND k.namespace = ? "
            fparams.append(namespace)
        fsql += "ORDER BY rank LIMIT ?"
        fparams.append(max(limit * 3, 30))
        try:
            frows = conn.execute(fsql, fparams).fetchall()
        except sqlite3.OperationalError:
            frows = []
        for row in frows:
            d = _sym_row_to_dict(row)
            if d["id"] in out:
                continue
            hay = _symptom_tokens(
                normalize_symptom(
                    f"{d.get('symptom') or ''} {d['title']} "
                    f"{' '.join(d['tags'])} {d['body']}"
                )
            )
            cov = (len(want & hay) / len(want)) if want else 0.0
            if cov <= 0:
                continue
            d["match"] = "text"
            d["score"] = round(0.5 * cov, 4)
            out[d["id"]] = d
    hits = sorted(
        out.values(),
        key=lambda d: (-d["score"], -(d.get("origin_ts") or d.get("ts") or 0.0)),
    )
    return hits[:limit]


def _attach_verdicts(
    conn: sqlite3.Connection, hits: list[dict], half_life_days: float
) -> None:
    """Read each hit's outcome from this store and stamp a ``verdict`` (and an
    ``outcome`` summary) on it, so a dead end can be surfaced rather than
    buried. Fails closed: a store with no outcome table leaves hits unrated."""
    if not hits:
        return
    try:
        if not _has_outcomes(conn):
            return
        entries, _files = _outcome_scores(conn, time.time(), half_life_days)
    except sqlite3.Error:
        return
    for h in hits:
        s = entries.get(h["id"])
        if s is not None:
            h["verdict"] = s.status
            h["outcome"] = s.summary()


@_locked
def knowledge_symptom_search(
    symptom_line: str,
    limit: int = 10,
    namespace: str | None = None,
    repo_root: str | Path | None = None,
) -> list[dict]:
    """Repo-store entries whose signature or body resembles ``symptom_line``,
    each carrying a ``verdict`` read from its outcomes (dead_end / corrected /
    useful / ...). Highest signature overlap first. The verdict lets the caller
    split confirmed diagnostics from known false trails without re-ranking."""
    conn = _get_conn(repo_root)
    hits = _symptom_hits(conn, symptom_line, limit, namespace=namespace)
    _attach_verdicts(conn, hits, _half_life_days(repo_root))
    return hits


def knowledge_symptom_search_ro(
    db_path: Path, symptom_line: str, limit: int = 10
) -> list[dict]:
    """Read-only symptom search against an arbitrary knowledge store (a shared
    store's knowledge.db). Fresh RO connection, verdicts attached from that
    store's own outcomes. Returns [] for an absent store or on any read error,
    so a search never fails over one unreadable store."""
    from cgh.state.knowledge_outcomes import DEFAULT_HALF_LIFE_DAYS

    db_path = Path(db_path)
    if not db_path.exists():
        return []
    from cgh.core.utils import ro_sqlite_uri

    try:
        conn = sqlite3.connect(ro_sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return []
    try:
        hits = _symptom_hits(conn, symptom_line, limit)
        _attach_verdicts(conn, hits, DEFAULT_HALF_LIFE_DAYS)
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return hits


_K_BASE_COLS = ("id", "kind", "title", "body", "tags", "file_refs", "session_id", "ts")
# Provenance of a promoted entry, read after the base columns. Only rows with
# promoted_at set get a `provenance` object, so native entries keep the shape
# they always had.
_K_PROV_COLS = (
    "promoted_at",
    "source_branch",
    "source_worktree",
    "source_pr",
    "source_commit",
    "source_session",
    "origin_id",
    "origin_ts",
)


def _k_cols(prefix: str = "") -> str:
    return ", ".join(prefix + c for c in (*_K_BASE_COLS, *_K_PROV_COLS))


def _knowledge_row_to_dict(row, score: float = 0.0) -> dict:
    out = {
        "id": row[0],
        "kind": row[1],
        "title": row[2],
        "body": row[3],
        "tags": [t for t in (row[4] or "").split(",") if t],
        "file_refs": [f for f in (row[5] or "").split(",") if f],
        "session_id": row[6],
        "ts": row[7] if len(row) > 7 else 0.0,
        "score": round(score, 4),
    }
    n_base = len(_K_BASE_COLS)
    if len(row) >= n_base + len(_K_PROV_COLS) and row[n_base] is not None:
        prov = dict(
            zip(_K_PROV_COLS, row[n_base : n_base + len(_K_PROV_COLS)], strict=True)
        )
        out["provenance"] = {k: v for k, v in prov.items() if v not in (None, "")}
    return out


@_locked
def log_call(
    tool: str,
    args: dict,
    latency_ms: float,
    result_size: int,
    success: bool = True,
    error: str | None = None,
    repo_root: str | Path | None = None,
    origin: str | None = None,
) -> None:
    """Record a tool call. ``origin`` is one of ORIGINS; anything else is
    stored as NULL (unknown)."""
    conn = _get_conn(repo_root)
    conn.execute(
        "INSERT INTO call_log (timestamp, tool, args, latency_ms, result_size, "
        "success, error, origin, repo_root) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            tool,
            json.dumps(args, default=str)[:2000],
            latency_ms,
            result_size,
            1 if success else 0,
            error,
            normalize_origin(origin),
            _cache_key(repo_root),
        ),
    )
    conn.commit()


@contextmanager
def track_call(
    tool: str,
    args: dict,
    repo_root: str | Path | None = None,
    origin: str | None = "internal",
):
    """
    Context manager that auto-logs a tool call with timing.

    Usage:
        with track_call("symbol_lookup", {"name": "foo"}) as tracker:
            result = do_work()
            tracker["result_size"] = len(result)
    """
    tracker = {"result_size": 0, "error": None, "success": True}
    t0 = time.perf_counter()
    try:
        yield tracker
    except Exception as exc:
        tracker["success"] = False
        tracker["error"] = str(exc)[:500]
        raise
    finally:
        latency = (time.perf_counter() - t0) * 1000
        log_call(
            tool=tool,
            args=args,
            latency_ms=round(latency, 2),
            result_size=tracker["result_size"],
            success=tracker["success"],
            error=tracker["error"],
            repo_root=repo_root,
            origin=origin,
        )


@_locked
def get_stats(repo_root: str | Path | None = None) -> dict:
    """Aggregate call statistics."""
    conn = _get_conn(repo_root)

    total = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    if total == 0:
        return {
            "total_calls": 0,
            "tools": {},
            "period": None,
            "by_origin": {},
            "repo_root": _cache_key(repo_root),
            "knowledge_recall": _recall_stats(conn),
        }

    # Per-tool stats
    rows = conn.execute("""
        SELECT tool,
               COUNT(*) as calls,
               ROUND(AVG(latency_ms), 2) as avg_latency_ms,
               ROUND(MIN(latency_ms), 2) as min_latency_ms,
               ROUND(MAX(latency_ms), 2) as max_latency_ms,
               SUM(result_size) as total_result_bytes,
               SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as errors
        FROM call_log
        GROUP BY tool
        ORDER BY calls DESC
    """).fetchall()

    tools = {}
    for row in rows:
        tools[row[0]] = {
            "calls": row[1],
            "avg_latency_ms": row[2],
            "min_latency_ms": row[3],
            "max_latency_ms": row[4],
            "total_result_bytes": row[5],
            "errors": row[6],
            "by_origin": {},
        }

    # Split by who triggered the call. NULL origin (rows logged before the
    # column existed, or an unrecognised value) is reported as "unknown".
    by_origin: dict[str, int] = {}
    for tool_name, origin, calls in conn.execute(
        "SELECT tool, COALESCE(origin, 'unknown'), COUNT(*) FROM call_log "
        "GROUP BY tool, COALESCE(origin, 'unknown')"
    ).fetchall():
        by_origin[origin] = by_origin.get(origin, 0) + calls
        if tool_name in tools:
            tools[tool_name]["by_origin"][origin] = calls

    # Time range
    first = conn.execute("SELECT MIN(timestamp) FROM call_log").fetchone()[0]
    last = conn.execute("SELECT MAX(timestamp) FROM call_log").fetchone()[0]

    # Errors
    error_count = conn.execute(
        "SELECT COUNT(*) FROM call_log WHERE success = 0"
    ).fetchone()[0]

    # Top queries (most recent 10)
    recent = conn.execute("""
        SELECT tool, args, latency_ms, result_size, success,
               datetime(timestamp, 'unixepoch', 'localtime') as ts, origin
        FROM call_log
        ORDER BY timestamp DESC
        LIMIT 10
    """).fetchall()

    recent_calls = [
        {
            "tool": r[0],
            "args": r[1][:100],
            "latency_ms": r[2],
            "result_size": r[3],
            "success": bool(r[4]),
            "timestamp": r[5],
            "origin": r[6],
        }
        for r in recent
    ]

    return {
        "total_calls": total,
        "error_count": error_count,
        "error_rate": f"{error_count / total * 100:.1f}%" if total > 0 else "0%",
        "period": {
            "first_call": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first)),
            "last_call": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)),
        },
        "tools": tools,
        "recent_calls": recent_calls,
        "by_origin": by_origin,
        "repo_root": _cache_key(repo_root),
        "knowledge_recall": _recall_stats(conn),
    }


@_locked
def get_logs(
    repo_root: str | Path | None = None,
    tool: str | None = None,
    limit: int = 50,
    errors_only: bool = False,
) -> list[dict]:
    """Get raw call logs with optional filters."""
    conn = _get_conn(repo_root)

    where_clauses = []
    params: list = []

    if tool:
        where_clauses.append("tool = ?")
        params.append(tool)
    if errors_only:
        where_clauses.append("success = 0")

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT tool, args, latency_ms, result_size, success, error, "
        f"datetime(timestamp, 'unixepoch', 'localtime') as ts, origin "
        f"FROM call_log {where} ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()

    return [
        {
            "tool": r[0],
            "args": r[1],
            "latency_ms": r[2],
            "result_size": r[3],
            "success": bool(r[4]),
            "error": r[5],
            "timestamp": r[6],
            "origin": r[7],
        }
        for r in rows
    ]


@_locked
def clear_logs(repo_root: str | Path | None = None) -> int:
    """Clear all call logs. Returns count of deleted rows."""
    conn = _get_conn(repo_root)
    count = conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0]
    conn.execute("DELETE FROM call_log")
    conn.commit()
    return count
