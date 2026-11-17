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
    valid_shared_namespace,
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
    # A ticket facet is never a shared store: it has no path under shared/.
    assert not is_shared_namespace("ticket:x")
    assert not (home / "home" / ".cgh" / "shared").exists()


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


# ---------------------------------------------------------------------------
# Security: name validation + path containment (F1 / F3)
# ---------------------------------------------------------------------------


def test_valid_shared_namespace_allowlist():
    assert valid_shared_namespace("org-platform")
    assert valid_shared_namespace("akonteo")
    assert valid_shared_namespace("team.1_x")
    # Traversal and separators are rejected.
    assert not valid_shared_namespace("../../etc")
    assert not valid_shared_namespace("a/b")
    assert not valid_shared_namespace("a\\b")
    assert not valid_shared_namespace(".hidden")
    assert not valid_shared_namespace("UPPER")
    assert not valid_shared_namespace("")


@pytest.mark.parametrize("bad", ["../../etc", "a/b", "..", ".x", "x" * 65])
def test_shared_store_path_refuses_traversal(home, bad):
    with pytest.raises(ValueError):
        shared_store_path(bad)


def test_traversing_namespace_is_not_shared_and_never_builds_a_path(home):
    # A traversal attempt is not classed as shared, so a write stays local and
    # no file is created outside the shared root.
    assert not is_shared_namespace("../../etc")


def test_record_rejects_an_invalid_shared_namespace(home):
    root = _repo(home)
    with pytest.raises(ValueError):
        knowledge_record("x", "y", kind="note", repo_root=root, namespace="a/b")


def test_config_drops_unsafe_namespace_names(home):
    global_dir = home / "home" / ".cgh"
    global_dir.mkdir(parents=True)
    (global_dir / "config.toml").write_text(
        '[cgh]\nknowledge_namespaces_global = ["org-platform", "../../etc", "a/b"]\n',
        encoding="utf-8",
    )
    from cgh.core.config import load_config

    cfg = load_config(_repo(home))
    # The unsafe names are silently dropped; the valid one survives.
    assert cfg.resolved_knowledge_namespaces() == ["org-platform"]


# ---------------------------------------------------------------------------
# Security: durable-authority kinds may not be shared (F4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["standing_instruction", "glossary"])
def test_shared_store_refuses_durable_authority_kinds(home, kind):
    root = _repo(home)
    with pytest.raises(ValueError):
        knowledge_record(
            "rule", "body", kind=kind, repo_root=root, namespace="org-platform"
        )
    # A standing_instruction stays recordable in the repo store.
    kid = knowledge_record("rule", "body", kind=kind, repo_root=root)
    assert kid > 0


# ---------------------------------------------------------------------------
# Security: a shared write may not retire another repo's row (F2)
# ---------------------------------------------------------------------------


def test_shared_supersede_is_refused_across_repos(home):
    root_a = _repo(home, "repo-a")
    root_b = _repo(home, "repo-b")
    # repo-a owns a shared note.
    a_id = knowledge_record(
        "shared decision", "body A", kind="decision",
        repo_root=root_a, namespace="org-platform", origin_repo="repo-a",
    )  # fmt: skip
    # repo-b tries to supersede repo-a's note in the same shared store.
    knowledge_record(
        "my version", "body B", kind="decision",
        repo_root=root_b, namespace="org-platform", origin_repo="repo-b",
        supersedes=a_id,
    )  # fmt: skip
    sp = shared_store_path("org-platform")
    row = (
        _get_conn_at_path(sp)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (a_id,))
        .fetchone()
    )
    assert row[0] is None  # repo-a's note was NOT retired by repo-b


def test_shared_supersede_allowed_for_the_owning_repo(home):
    root = _repo(home, "repo-a")
    first = knowledge_record(
        "shared decision", "old body", kind="decision",
        repo_root=root, namespace="org-platform", origin_repo="repo-a",
    )  # fmt: skip
    knowledge_record(
        "shared decision", "new body", kind="decision",
        repo_root=root, namespace="org-platform", origin_repo="repo-a",
        supersedes=first,
    )  # fmt: skip
    sp = shared_store_path("org-platform")
    row = (
        _get_conn_at_path(sp)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (first,))
        .fetchone()
    )
    assert row[0] is not None  # the owning repo may retire its own note


def test_shared_supersede_refused_when_caller_spoofs_origin_repo(home):
    root_a = _repo(home, "repo-a")
    root_b = _repo(home, "repo-b")
    a_id = knowledge_record(
        "shared decision", "body A", kind="decision",
        repo_root=root_a, namespace="org-platform", origin_repo="repo-a",
    )  # fmt: skip
    # repo-b spoofs origin_repo="repo-a" to look like the owner; ownership is
    # the server identity of the calling repo (repo-b), so it is still refused.
    knowledge_record(
        "my version", "body B", kind="decision",
        repo_root=root_b, namespace="org-platform", origin_repo="repo-a",
        supersedes=a_id,
    )  # fmt: skip
    sp = shared_store_path("org-platform")
    row = (
        _get_conn_at_path(sp)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (a_id,))
        .fetchone()
    )
    assert row[0] is None


def test_shared_supersede_refused_when_target_has_no_owner(home):
    root = _repo(home, "repo-a")
    # A target with no owner (origin_repo stored NULL) is owned by nobody, so
    # even the same repo cannot retire it through the shared supersede path.
    ownerless = knowledge_record(
        "shared decision", "old body", kind="decision",
        repo_root=root, namespace="org-platform", origin_repo="",
    )  # fmt: skip
    knowledge_record(
        "shared decision", "new body", kind="decision",
        repo_root=root, namespace="org-platform", origin_repo="repo-a",
        supersedes=ownerless,
    )  # fmt: skip
    sp = shared_store_path("org-platform")
    row = (
        _get_conn_at_path(sp)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (ownerless,))
        .fetchone()
    )
    assert row[0] is None


def _get_conn_at_path(db_path):
    from cgh.state.call_log import _get_conn_at

    return _get_conn_at(db_path)
