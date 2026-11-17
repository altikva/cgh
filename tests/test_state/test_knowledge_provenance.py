# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Provenance and namespace columns on the knowledge store: the
#              guarded schema additions, knowledge_record's new fields and their
#              backward-compatible defaults, symptom normalisation, and the
#              server-side auto-fill of author and origin_repo.

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cgh.state.call_log import (
    _get_conn,
    git_user_name,
    knowledge_list,
    knowledge_record,
    normalize_symptom,
    repo_slug,
    reset_for_tests,
)


def _store(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".cgh").mkdir(parents=True)
    return root


def _git_repo(path: Path, *, user: str, remote: str | None = None) -> Path:
    (path / ".cgh").mkdir(parents=True)
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=path, check=True, capture_output=True, env=env
    )
    run("init")
    run("config", "user.name", user)
    if remote is not None:
        run("remote", "add", "origin", remote)
    return path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_new_store_has_provenance_columns_and_indexes(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "fresh")
    conn = _get_conn(root)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge)")}
    assert {
        "namespace",
        "origin_repo",
        "ticket",
        "author",
        "symptom",
        "symptom_norm",
    } <= cols
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(knowledge)")}
    assert {"idx_knowledge_namespace", "idx_knowledge_symptom"} <= indexes
    fts_cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_fts)")}
    assert "symptom" in fts_cols


# ---------------------------------------------------------------------------
# knowledge_record defaults and new fields
# ---------------------------------------------------------------------------


def test_old_fts_without_symptom_is_rebuilt_and_rows_stay_searchable(tmp_path):
    import sqlite3

    reset_for_tests()
    root = _store(tmp_path, "fts-migrate")
    # A store with the previous 4-column FTS, populated through its trigger.
    conn = sqlite3.connect(root / ".cgh" / "call_log.db")
    conn.execute(
        "CREATE TABLE knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
        "body TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT 'note', file_refs TEXT NOT NULL DEFAULT '', "
        "ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, body, tags, "
        "kind UNINDEXED, content='knowledge', content_rowid='id')"
    )
    conn.execute(
        "CREATE TRIGGER knowledge_ai AFTER INSERT ON knowledge BEGIN "
        "INSERT INTO knowledge_fts(rowid, title, body, tags, kind) "
        "VALUES (new.id, new.title, new.body, new.tags, new.kind); END"
    )
    conn.execute(
        "INSERT INTO knowledge(title, body, kind, ts) "
        "VALUES ('watcher gap', 'scan_meta never advances', 'gotcha', 1)"
    )
    conn.commit()
    conn.close()

    migrated = _get_conn(root)
    fts_cols = {r[1] for r in migrated.execute("PRAGMA table_info(knowledge_fts)")}
    assert "symptom" in fts_cols
    # The pre-migration row survived the drop + rebuild and is still searchable.
    hit = migrated.execute(
        "SELECT k.title FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
        "WHERE knowledge_fts MATCH 'scan_meta'"
    ).fetchone()
    assert hit == ("watcher gap",)


def test_record_defaults_leave_current_behaviour(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "def")
    kid = knowledge_record("plain", "body", kind="gotcha", repo_root=root)
    row = (
        _get_conn(root)
        .execute(
            "SELECT namespace, origin_repo, ticket, author, symptom, symptom_norm "
            "FROM knowledge WHERE id = ?",
            (kid,),
        )
        .fetchone()
    )
    assert row == ("repo", None, None, None, None, None)
    # The existing read path is untouched: the entry lists as it always did.
    assert [e["title"] for e in knowledge_list(repo_root=root)] == ["plain"]


def test_record_stores_explicit_provenance_and_normalises_symptom(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "prov")
    # A ticket facet stays in the repo store (only a shared namespace routes
    # out to ~/.cgh/shared/), so the provenance columns are read back locally.
    kid = knowledge_record(
        "docker preflight",
        "echo of the preflight, not a real missing docker",
        kind="gotcha",
        repo_root=root,
        namespace="ticket:media#412",
        origin_repo="media-service",
        ticket="media#412",
        author="media-manager",
        symptom="docker is not installed at /usr/local/bin/docker (pid 12345)",
    )
    row = (
        _get_conn(root)
        .execute(
            "SELECT namespace, origin_repo, ticket, author, symptom, symptom_norm "
            "FROM knowledge WHERE id = ?",
            (kid,),
        )
        .fetchone()
    )
    assert row[0] == "ticket:media#412"
    assert row[1] == "media-service"
    assert row[2] == "media#412"
    assert row[3] == "media-manager"
    assert row[4] == "docker is not installed at /usr/local/bin/docker (pid 12345)"
    # Path and number gummed, the signifying tokens kept.
    assert row[5] == "docker is not installed at <path> (pid <n>)"


def test_blank_namespace_falls_back_to_repo(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "ns")
    kid = knowledge_record("x", "y", repo_root=root, namespace="")
    ns = (
        _get_conn(root)
        .execute("SELECT namespace FROM knowledge WHERE id = ?", (kid,))
        .fetchone()[0]
    )
    assert ns == "repo"


def test_symptom_is_indexed_in_fts(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "fts")
    knowledge_record(
        "title with no match",
        "body with no match",
        kind="gotcha",
        repo_root=root,
        symptom="SERVICE_DISABLED cloudresourcemanager",
    )
    hit = (
        _get_conn(root)
        .execute(
            "SELECT k.title FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH 'SERVICE_DISABLED'"
        )
        .fetchone()
    )
    assert hit == ("title with no match",)


# ---------------------------------------------------------------------------
# Symptom normalisation helper
# ---------------------------------------------------------------------------


def test_normalize_symptom_gums_volatile_fragments():
    assert normalize_symptom("") == ""
    assert (
        normalize_symptom("failed at /Users/joy/app/main.py line 42")
        == "failed at <path> line <n>"
    )
    assert (
        normalize_symptom("conn 0x7ffee uuid 550e8400-e29b-41d4-a716-446655440000")
        == "conn <hex> uuid <hex>"
    )
    assert (
        normalize_symptom("crash at 2026-11-17T06:01:52+00:00 on worker 3")
        == "crash at <ts> on worker <n>"
    )
    # An all-digit run is a number, not hex; a sha with letters is hex.
    assert normalize_symptom("build 1234567 sha deadbeef1") == "build <n> sha <hex>"


# ---------------------------------------------------------------------------
# Server-side auto-fill of author and origin_repo
# ---------------------------------------------------------------------------


def test_git_user_name_and_repo_slug(tmp_path):
    reset_for_tests()
    repo = _git_repo(
        tmp_path / "proj",
        user="Jane Dev",
        remote="https://github.com/altikva/demo-repo.git",
    )
    assert git_user_name(repo) == "Jane Dev"
    assert repo_slug(repo) == "demo-repo"


def test_repo_slug_falls_back_to_directory_name(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "no-git-here")
    # Outside a work tree `git remote get-url` fails, so the slug is the
    # directory name. (git_user_name may still resolve from global config, which
    # is the real user's name and intentionally not isolated here.)
    assert repo_slug(root) == "no-git-here"
    assert isinstance(git_user_name(root), str)


class _FakeMCP:
    """Captures the functions a register() call decorates with @mcp.tool()."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def test_server_record_autofills_author_and_origin_repo(tmp_path, monkeypatch):
    reset_for_tests()
    import cgh.server as _srv
    from cgh.server.tools_knowledge import register

    repo = _git_repo(
        tmp_path / "svc",
        user="Owner Session",
        remote="git@github.com:altikva/widget-api.git",
    )
    mcp = _FakeMCP()
    register(mcp)
    monkeypatch.setattr(_srv, "_root", str(repo))

    mcp.tools["knowledge_record"](title="t", body="b", kind="gotcha")
    row = (
        _get_conn(repo)
        .execute("SELECT author, origin_repo, namespace FROM knowledge")
        .fetchone()
    )
    assert row == ("Owner Session", "widget-api", "repo")


def test_server_record_keeps_explicit_author_and_origin_repo(tmp_path, monkeypatch):
    reset_for_tests()
    import cgh.server as _srv
    from cgh.server.tools_knowledge import register

    repo = _git_repo(
        tmp_path / "svc2",
        user="Owner Session",
        remote="git@github.com:altikva/widget-api.git",
    )
    mcp = _FakeMCP()
    register(mcp)
    monkeypatch.setattr(_srv, "_root", str(repo))

    mcp.tools["knowledge_record"](
        title="t", body="b", kind="gotcha", author="someone-else", origin_repo="other"
    )
    row = (
        _get_conn(repo).execute("SELECT author, origin_repo FROM knowledge").fetchone()
    )
    assert row == ("someone-else", "other")
