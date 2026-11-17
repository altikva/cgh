# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The MCP knowledge tools read subscribed shared stores by default
#              (L9.2): knowledge_search and knowledge_list merge the repo store
#              with the subscribed fleet without scope="all", tag each shared
#              hit with its namespace, the namespace= filter narrows to one
#              store or to the repo, and duplicates are collapsed by origin.

from __future__ import annotations

import json

import pytest

import cgh.server as _srv
from cgh.server.tools_knowledge import _dedup_by_origin, register
from cgh.state.call_log import reset_for_tests


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    monkeypatch.setattr(_srv, "_root", root)
    m = _FakeMcp()
    register(m)
    yield m.tools, root
    reset_for_tests()


def test_search_reads_the_fleet_by_default(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention",
        body="all handlers return an envelope",
        kind="decision",
        namespace="org-platform",
    )
    t["knowledge_record"](
        title="repo note",
        body="local handlers return an envelope",
        kind="decision",
    )
    out = json.loads(t["knowledge_search"](query="handlers"))
    by_title = {h["title"]: h for h in out["hits"]}
    assert "fleet convention" in by_title  # shared, read without scope="all"
    assert "repo note" in by_title
    shared = by_title["fleet convention"]
    assert shared["namespace"] == "org-platform"
    assert shared["scope"] == "org-platform"


def test_namespace_filter_narrows_to_one_shared_store(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="handlers return an envelope",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    t["knowledge_record"](
        title="repo note", body="handlers return an envelope", kind="decision",
    )  # fmt: skip
    out = json.loads(t["knowledge_search"](query="handlers", namespace="org-platform"))
    assert {h["title"] for h in out["hits"]} == {"fleet convention"}


def test_namespace_repo_filter_excludes_the_fleet(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="handlers return an envelope",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    t["knowledge_record"](
        title="repo note", body="handlers return an envelope", kind="decision",
    )  # fmt: skip
    out = json.loads(t["knowledge_search"](query="handlers", namespace="repo"))
    assert {h["title"] for h in out["hits"]} == {"repo note"}


def test_list_merges_the_fleet_by_default(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="shared body",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    t["knowledge_record"](title="repo note", body="local body", kind="decision")
    out = json.loads(t["knowledge_list"]())
    by_title = {e["title"]: e for e in out["entries"]}
    assert {"fleet convention", "repo note"} <= set(by_title)
    assert by_title["fleet convention"]["namespace"] == "org-platform"


def test_list_namespace_filter_lists_only_that_store(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="shared body",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    t["knowledge_record"](title="repo note", body="local body", kind="decision")
    out = json.loads(t["knowledge_list"](namespace="org-platform"))
    assert {e["title"] for e in out["entries"]} == {"fleet convention"}


# ---------------------------------------------------------------------------
# De-duplication by origin
# ---------------------------------------------------------------------------


def test_dedup_keeps_the_higher_scope_and_flags_the_local_copy():
    local = {"id": 1, "title": "t", "origin_repo": "r", "origin_id": 7}
    shared = {
        "id": 3, "title": "t", "origin_repo": "r", "origin_id": 7,
        "scope": "org-platform", "namespace": "org-platform",
    }  # fmt: skip
    out = _dedup_by_origin([local, shared])
    assert len(out) == 1
    assert out[0]["scope"] == "org-platform"  # the org copy wins
    assert out[0]["local_copy"] is True


def test_dedup_leaves_rows_without_an_origin_key_untouched():
    a = {"id": 1, "title": "a"}
    b = {"id": 2, "title": "b", "origin_repo": "r"}  # no origin_id
    out = _dedup_by_origin([a, b])
    assert out == [a, b]


# ---------------------------------------------------------------------------
# Security: shared hits are marked untrusted (F4)
# ---------------------------------------------------------------------------


def test_shared_search_hits_are_marked_untrusted(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="handlers return an envelope",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    t["knowledge_record"](
        title="repo note", body="local handlers return an envelope", kind="decision"
    )
    out = json.loads(t["knowledge_search"](query="handlers"))
    shared = next(h for h in out["hits"] if h["title"] == "fleet convention")
    assert shared["untrusted"] is True
    # A local repo hit carries no untrusted marker.
    local = next(h for h in out["hits"] if h["title"] == "repo note")
    assert "untrusted" not in local


def test_shared_list_entries_are_marked_untrusted(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="shared body",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    out = json.loads(t["knowledge_list"]())
    shared = next(e for e in out["entries"] if e["title"] == "fleet convention")
    assert shared["untrusted"] is True


def test_record_rejects_standing_instruction_in_shared_namespace(tools):
    t, _ = tools
    raw = t["knowledge_record"](
        title="always do X",
        body="rule",
        kind="standing_instruction",
        namespace="org-platform",
    )
    assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# Security: shared ids are namespaced and never address the repo store (IDOR)
# ---------------------------------------------------------------------------


def test_shared_hit_id_is_namespaced(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="handlers return an envelope",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    out = json.loads(t["knowledge_search"](query="handlers"))
    shared = next(h for h in out["hits"] if h["title"] == "fleet convention")
    # Not a bare int reusable against the repo store.
    assert isinstance(shared["id"], str)
    assert shared["id"].startswith("org-platform:")


def test_entry_id_tools_reject_a_shared_id(tools):
    t, _ = tools
    t["knowledge_record"](
        title="fleet convention", body="handlers return an envelope",
        kind="decision", namespace="org-platform",
    )  # fmt: skip
    out = json.loads(t["knowledge_search"](query="handlers"))
    shared_id = next(h["id"] for h in out["hits"] if h["title"] == "fleet convention")
    assert "error" in json.loads(t["knowledge_list"](entry_id=shared_id))
    assert "error" in json.loads(t["knowledge_forget"](entry_id=shared_id))
    assert "error" in json.loads(
        t["knowledge_outcome"](verdict="useful", entry_id=shared_id)
    )
