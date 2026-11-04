# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: knowledge_list answers with briefs: a default page of ten
#              stays near 2.5 KB and says how many briefs it left out, the
#              brief carries id, title, kind, tags, date, outcome status and
#              the start of the body, entry_id still reads one entry whole,
#              paging and filters keep working, and an explicit larger limit
#              only meets the 10 KB ceiling.

from __future__ import annotations

import inspect
import json

import pytest

import codegraph.server as _srv
from codegraph.server._budget import ANSWER_MAX_BYTES
from codegraph.server.tools_knowledge import LIST_TARGET_BYTES, register
from codegraph.state import knowledge_recall as kr
from codegraph.state.call_log import (
    knowledge_outcome_record,
    knowledge_record,
    reset_for_tests,
)


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def store(tmp_path, monkeypatch):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr(_srv, "_root", tmp_path)
    yield tmp_path
    reset_for_tests()


@pytest.fixture
def klist(store):
    m = _FakeMcp()
    register(m)
    return m.tools["knowledge_list"]


def _fill(store, n: int, kind: str = "gotcha") -> list[int]:
    ids = []
    for i in range(n):
        body = "\n".join(
            f"line {j} of note {i} about the stale lock" for j in range(60)
        )
        ids.append(
            knowledge_record(
                f"{kind} note number {i} on a stale owner lock",
                body,
                kind=kind,
                tags="papercut,lock,owner",
                file_refs="codegraph/state/ipc.py",
                repo_root=store,
            )
        )
    return ids


def test_default_limit_is_ten(klist):
    params = inspect.signature(klist).parameters
    assert params["limit"].default == kr.LIST_DEFAULT_LIMIT == 10


def test_default_answer_stays_near_the_target(store, klist):
    _fill(store, 40)
    raw = klist()
    assert len(raw.encode("utf-8")) <= LIST_TARGET_BYTES
    out = json.loads(raw)
    assert out["total"] == 40
    assert out["returned"] == len(out["entries"]) >= 5
    assert out["returned"] + out.get("dropped", 0) == 10
    if out.get("dropped"):
        assert "offset" in out["hint"]
    assert out["has_more"] is True
    assert out["next_offset"] == out["returned"]


def test_entries_are_briefs(store, klist):
    ids = _fill(store, 3)
    knowledge_outcome_record("useful", entry_id=ids[0], repo_root=store)
    out = json.loads(klist())
    assert out["returned"] == 3
    first = out["entries"][0]
    assert first["id"] == ids[0]  # useful entries tier first
    assert set(first) <= {
        "id",
        "title",
        "kind",
        "tags",
        "recorded",
        "outcome",
        "promoted",
        "body",
        "body_clipped",
    }
    assert first["outcome"] == {"status": "useful"}
    assert first["recorded"].endswith("Z")
    assert first["tags"] == ["papercut", "lock", "owner"]
    assert len(first["body"]) <= kr.BRIEF_BODY + len(" [...]")
    assert first["body_clipped"] is True
    assert "file_refs" not in first and "session_id" not in first
    assert "entry_id=" in out["read_more"]


def test_entry_id_reads_the_entry_whole(store, klist):
    ids = _fill(store, 3)
    out = json.loads(klist(entry_id=ids[1]))
    entry = out["entries"][0]
    assert entry["id"] == ids[1]
    assert entry["body"].count("\n") == 59
    assert entry["file_refs"] == ["codegraph/state/ipc.py"]
    assert "body_clipped" not in entry


def test_paging_covers_every_entry_once(store, klist):
    ids = set(_fill(store, 25))
    seen: list[int] = []
    offset = 0
    for _ in range(25):
        out = json.loads(klist(offset=offset))
        seen += [e["id"] for e in out["entries"]]
        if not out["has_more"]:
            break
        offset = out["next_offset"]
    assert len(seen) == len(set(seen)) == 25
    assert set(seen) == ids


def test_filters_still_apply(store, klist):
    _fill(store, 4, kind="gotcha")
    _fill(store, 2, kind="decision")
    knowledge_record("s", "b", kind="note", session_id="sess-1", repo_root=store)
    out = json.loads(klist(kind="decision"))
    assert out["total"] == 2 and {e["kind"] for e in out["entries"]} == {"decision"}
    out = json.loads(klist(session_id="sess-1"))
    assert [e["title"] for e in out["entries"]] == ["s"]
    assert out["has_more"] is False and "dropped" not in out


def test_explicit_limit_is_only_cut_at_the_ceiling(store, klist):
    _fill(store, 60)
    raw = klist(limit=15)
    assert LIST_TARGET_BYTES < len(raw.encode("utf-8")) <= ANSWER_MAX_BYTES
    out = json.loads(raw)
    assert out["returned"] == 15 and "dropped" not in out
    assert all("body" in e for e in out["entries"])
    big = klist(limit=60)
    assert len(big.encode("utf-8")) <= ANSWER_MAX_BYTES
    out = json.loads(big)
    assert out["truncated"] is True and out["next_offset"] == out["returned"]
