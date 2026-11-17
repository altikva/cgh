# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared knowledge stores and namespace subscription (L9.2): a
#              shared namespace routes writes to ~/.cgh/shared/<name>/knowledge.db
#              with the same schema as a repo store, an absent store is ignored,
#              the read-only reader exposes provenance, the local namespace
#              filter restricts a search or list, and config resolves the
#              subscribed set from the global fleet plus the project.

from __future__ import annotations

from pathlib import Path

import pytest

from cgh.state.call_log import (
    _get_conn,
    is_shared_namespace,
    knowledge_list,
    knowledge_record,
    knowledge_search,
    knowledge_search_ro,
    reset_for_tests,
    shared_store_path,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect GLOBAL_DIR so shared stores land under the test's tmp dir."""
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    yield tmp_path
    reset_for_tests()


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / ".cgh").mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Namespace classification
# ---------------------------------------------------------------------------


def test_is_shared_namespace_classifies_names():
    assert is_shared_namespace("org-platform")
    assert is_shared_namespace("akonteo")
    assert not is_shared_namespace("repo")
    assert not is_shared_namespace("branch")
    assert not is_shared_namespace("ticket:media#412")
    assert not is_shared_namespace("")
    assert not is_shared_namespace("  ")


# ---------------------------------------------------------------------------
# Write routing
# ---------------------------------------------------------------------------


def test_shared_namespace_routes_to_shared_store(home):
    root = _repo(home)
    knowledge_record(
        "fleet convention",
        "all handlers return X",
        kind="decision",
        repo_root=root,
        namespace="org-platform",
        origin_repo="media-service",
        author="media-manager",
    )
    # Nothing landed in the repo store.
    assert _get_conn(root).execute("SELECT count(*) FROM knowledge").fetchone()[0] == 0
    # The shared store exists and holds the row.
    sp = shared_store_path("org-platform")
    assert sp == home / "home" / ".cgh" / "shared" / "org-platform" / "knowledge.db"
    assert sp.exists()
    hits = knowledge_search_ro(sp, "handlers", with_provenance=True)
    assert len(hits) == 1
    assert hits[0]["title"] == "fleet convention"
    assert hits[0]["origin_repo"] == "media-service"
    assert hits[0]["namespace"] == "org-platform"
    assert hits[0]["origin_id"] is None  # a native entry has no promotion origin


def test_ticket_namespace_stays_in_the_repo_store(home):
    root = _repo(home)
    knowledge_record(
        "ticket decision", "body", kind="decision",
        repo_root=root, namespace="ticket:x",
    )  # fmt: skip
    assert _get_conn(root).execute("SELECT count(*) FROM knowledge").fetchone()[0] == 1
    assert not shared_store_path("ticket:x").exists()


def test_shared_store_created_on_first_write_only(home):
    # No write yet: the store is absent and reads as empty, silently.
    assert not shared_store_path("akonteo").exists()
    assert knowledge_search_ro(shared_store_path("akonteo"), "anything") == []
    root = _repo(home)
    knowledge_record("x", "y", kind="note", repo_root=root, namespace="akonteo")
    assert shared_store_path("akonteo").exists()


# ---------------------------------------------------------------------------
# Read-only reader provenance
# ---------------------------------------------------------------------------


def test_ro_reader_without_provenance_keeps_the_old_shape(home):
    root = _repo(home)
    knowledge_record("plain", "body", kind="note", repo_root=root)
    reset_for_tests()
    hits = knowledge_search_ro(root / ".cgh" / "call_log.db", "plain")
    assert len(hits) == 1
    assert "origin_repo" not in hits[0]
    assert "namespace" not in hits[0]


def test_ro_reader_empty_query_lists_everything(home):
    root = _repo(home)
    for i in range(3):
        knowledge_record(f"note {i}", "body", kind="note", repo_root=root)
    reset_for_tests()
    hits = knowledge_search_ro(root / ".cgh" / "call_log.db", "", limit=10)
    assert len(hits) == 3


# ---------------------------------------------------------------------------
# Local namespace filter
# ---------------------------------------------------------------------------


def test_namespace_filter_on_search(home):
    root = _repo(home)
    knowledge_record(
        "stale lock in ticket", "body about the lock", kind="gotcha",
        tags="lock", repo_root=root, namespace="ticket:a",
    )  # fmt: skip
    knowledge_record(
        "stale lock in repo", "body about the lock", kind="gotcha",
        tags="lock", repo_root=root,
    )  # fmt: skip
    assert len(knowledge_search("lock", repo_root=root, recall=True)) == 2
    only_ticket = knowledge_search(
        "lock", repo_root=root, recall=True, namespace="ticket:a"
    )
    assert [h["title"] for h in only_ticket] == ["stale lock in ticket"]
    only_repo = knowledge_search("lock", repo_root=root, recall=True, namespace="repo")
    assert [h["title"] for h in only_repo] == ["stale lock in repo"]


def test_namespace_filter_on_list(home):
    root = _repo(home)
    knowledge_record("alpha", "a", kind="note", repo_root=root, namespace="ticket:a")
    knowledge_record("beta", "b", kind="note", repo_root=root)
    assert len(knowledge_list(repo_root=root)) == 2
    only_ticket = knowledge_list(repo_root=root, namespace="ticket:a")
    assert [e["title"] for e in only_ticket] == ["alpha"]


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_default_global_namespace_is_org_platform(home):
    from cgh.core.config import load_config

    cfg = load_config(_repo(home))
    assert cfg.knowledge_namespaces_global == ["org-platform"]
    assert cfg.resolved_knowledge_namespaces() == ["org-platform"]


def test_resolution_is_global_union_project(home):
    global_dir = home / "home" / ".cgh"
    global_dir.mkdir(parents=True)
    (global_dir / "config.toml").write_text(
        '[cgh]\nknowledge_namespaces_global = ["org-platform"]\n', encoding="utf-8"
    )
    proj = _repo(home)
    (proj / ".cgh" / "config.toml").write_text(
        '[cgh]\nknowledge_namespaces = ["akonteo", "org-platform"]\n', encoding="utf-8"
    )
    from cgh.core.config import load_config

    cfg = load_config(proj)
    # Global first, project appended, de-duplicated, order preserved.
    assert cfg.resolved_knowledge_namespaces() == ["org-platform", "akonteo"]


def test_unsubscribe_via_empty_global(home):
    global_dir = home / "home" / ".cgh"
    global_dir.mkdir(parents=True)
    (global_dir / "config.toml").write_text(
        "[cgh]\nknowledge_namespaces_global = []\n", encoding="utf-8"
    )
    from cgh.core.config import load_config

    cfg = load_config(_repo(home))
    assert cfg.resolved_knowledge_namespaces() == []
