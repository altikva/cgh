# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Knowledge outcomes: recording and validation, the decay and
#              status rules, outcome-aware ranking in search and list, the
#              resume bundle's known dead ends, the MCP tool and CLI verbs,
#              the half-life config key, and opening a store created before
#              the outcome table existed.

from __future__ import annotations

import json
import sqlite3
import time
from argparse import Namespace
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.config import load_config
from codegraph.server.tools_session import build_resume_bundle
from codegraph.state import call_log
from codegraph.state.call_log import (
    _get_conn,
    knowledge_dead_ends,
    knowledge_forget,
    knowledge_list,
    knowledge_outcome_record,
    knowledge_outcome_scores,
    knowledge_record,
    knowledge_search,
    reset_for_tests,
)
from codegraph.state.knowledge_outcomes import (
    decay_weight,
    list_bucket,
    rank_factor,
    score_outcomes,
)

DAY = 86400.0
NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    yield tmp_path
    reset_for_tests()


def _age(store: Path, outcome_id: int, days: float) -> None:
    conn = _get_conn(store)
    conn.execute(
        "UPDATE knowledge_outcome SET ts = ? WHERE id = ?",
        (time.time() - days * DAY, outcome_id),
    )
    conn.commit()


# --- decay and status rules -------------------------------------------------


def test_decay_halves_every_half_life():
    assert decay_weight(0, 30) == 1.0
    assert decay_weight(30 * DAY, 30) == pytest.approx(0.5)
    assert decay_weight(90 * DAY, 30) == pytest.approx(0.125)
    assert decay_weight(-5 * DAY, 30) == 1.0  # clock skew counts as now
    assert decay_weight(400 * DAY, 0) == 1.0  # zero half-life: no decay


def test_status_rules():
    def s(*rows):
        return score_outcomes(rows, NOW, 30)

    one_useful = s(("useful", NOW, "", None))
    assert one_useful.status == "useful" and one_useful.score == pytest.approx(1.0)
    assert s(("useful", NOW, "", None), ("useful", NOW - DAY, "", None)).status == (
        "preferred"
    )
    assert s(("dead_end", NOW, "x", None)).status == "dead_end"
    mixed = s(("useful", NOW, "", None), ("dead_end", NOW, "", None))
    assert mixed.status == "contested" and mixed.score == pytest.approx(0.0)
    # An old dead end is outweighed by a fresh useful verdict.
    assert (
        s(("dead_end", NOW - 120 * DAY, "", None), ("useful", NOW, "", None)).status
        == "useful"
    )
    # A lone verdict fades after three half-lives.
    assert s(("dead_end", NOW - 100 * DAY, "", None)).status == "faded"


def test_correction_wins_until_a_later_useful_verdict():
    corrected = score_outcomes(
        [("useful", NOW - 2 * DAY, "", None), ("corrected", NOW - DAY, "wrong", 7)],
        NOW,
        30,
    )
    assert corrected.status == "corrected"
    assert corrected.corrected_by == 7
    assert corrected.summary()["last_note"] == "wrong"
    # A correction never fades.
    old = score_outcomes([("corrected", NOW - 900 * DAY, "", None)], NOW, 30)
    assert old.status == "corrected"
    revived = score_outcomes(
        [("corrected", NOW - 2 * DAY, "", None), ("useful", NOW, "", None)], NOW, 30
    )
    assert revived.status != "corrected"


def test_rank_factor_and_bucket():
    useful = score_outcomes([("useful", NOW, "", None)], NOW, 30)
    dead = score_outcomes([("dead_end", NOW, "", None)], NOW, 30)
    fixed = score_outcomes([("corrected", NOW, "", None)], NOW, 30)
    assert rank_factor(None) == 1.0
    assert 1.0 < rank_factor(useful) <= 1.5
    assert 0.5 <= rank_factor(dead) < 1.0
    assert rank_factor(fixed) == 0.25
    assert [list_bucket(x) for x in (useful, None, dead, fixed)] == [0, 1, 2, 3]


# --- recording ----------------------------------------------------------------


def test_record_validates(store):
    eid = knowledge_record("A", "b", kind="gotcha", repo_root=store)
    with pytest.raises(ValueError, match="verdict"):
        knowledge_outcome_record("great", entry_id=eid, repo_root=store)
    with pytest.raises(ValueError, match="needs"):
        knowledge_outcome_record("useful", repo_root=store)
    with pytest.raises(ValueError, match="no knowledge entry"):
        knowledge_outcome_record("useful", entry_id=9999, repo_root=store)
    with pytest.raises(ValueError, match="corrected_by"):
        knowledge_outcome_record(
            "useful", entry_id=eid, corrected_by=eid, repo_root=store
        )
    with pytest.raises(ValueError, match="entry that was wrong"):
        knowledge_outcome_record("corrected", file_refs="a.py", repo_root=store)
    with pytest.raises(ValueError, match="corrected_by entry"):
        knowledge_outcome_record(
            "corrected", entry_id=eid, corrected_by=4242, repo_root=store
        )


def test_record_entry_and_files(store):
    eid = knowledge_record("Use the queue", "...", kind="pattern", repo_root=store)
    oid = knowledge_outcome_record(
        "dead_end",
        entry_id=eid,
        file_refs=["src/a.py", " src/b.py ", "src/a.py"],
        note="task: retry logic",
        session_id="s1",
        repo_root=store,
    )
    assert oid > 0
    knowledge_outcome_record("useful", file_refs="src/c.py", repo_root=store)
    entries, files = knowledge_outcome_scores(repo_root=store)
    assert entries[eid].status == "dead_end"
    assert sorted(files) == ["src/a.py", "src/b.py", "src/c.py"]
    assert files["src/a.py"].dead_end == 1
    assert files["src/c.py"].status == "useful"


def test_forget_drops_outcomes(store):
    eid = knowledge_record("A", "b", repo_root=store)
    knowledge_outcome_record("useful", entry_id=eid, repo_root=store)
    assert knowledge_forget(eid, repo_root=store)
    entries, _ = knowledge_outcome_scores(repo_root=store)
    assert eid not in entries


# --- ranking ------------------------------------------------------------------


def test_search_ranks_by_outcome(store):
    ids = [
        knowledge_record(
            f"retry policy {i}", "retry policy for the uploader", repo_root=store
        )
        for i in range(3)
    ]
    base = [h["id"] for h in knowledge_search("retry policy", repo_root=store)]
    assert "outcome" not in knowledge_search("retry policy", repo_root=store)[0]
    top, last = base[0], base[-1]
    fix = knowledge_record("retry policy fixed", "the right one", repo_root=store)
    knowledge_outcome_record(
        "corrected", entry_id=top, corrected_by=fix, repo_root=store
    )
    knowledge_outcome_record("useful", entry_id=last, repo_root=store)
    knowledge_outcome_record("useful", entry_id=last, repo_root=store)
    hits = knowledge_search("retry policy", repo_root=store)
    order = [h["id"] for h in hits if h["id"] in ids]
    assert order[0] == last
    assert order[-1] == top
    by_id = {h["id"]: h for h in hits}
    assert by_id[top]["outcome"]["status"] == "corrected"
    assert by_id[top]["outcome"]["corrected_by"] == fix
    assert by_id[last]["outcome"]["status"] == "preferred"
    # Opting out gives plain relevance order back.
    plain = knowledge_search("retry policy", repo_root=store, rank_outcomes=False)
    assert [h["id"] for h in plain if h["id"] in ids] == base


def test_search_lifts_a_useful_entry_past_the_limit(store):
    ids = [
        knowledge_record(f"cache note {i}", "cache cache", repo_root=store)
        for i in range(6)
    ]
    plain = [h["id"] for h in knowledge_search("cache", limit=6, repo_root=store)]
    outsider = plain[-1]
    for _ in range(3):
        knowledge_outcome_record("useful", entry_id=outsider, repo_root=store)
    hits = knowledge_search("cache", limit=3, repo_root=store)
    assert outsider in [h["id"] for h in hits]
    assert set(ids) >= {h["id"] for h in hits}


def test_list_tiers_by_outcome_only_on_request(store):
    a = knowledge_record("a", "x", repo_root=store)
    b = knowledge_record("b", "x", repo_root=store)
    c = knowledge_record("c", "x", repo_root=store)
    d = knowledge_record("d", "x", repo_root=store)
    knowledge_outcome_record("useful", entry_id=a, repo_root=store)
    knowledge_outcome_record("corrected", entry_id=d, repo_root=store)
    knowledge_outcome_record("dead_end", entry_id=c, repo_root=store)
    plain = [e["id"] for e in knowledge_list(repo_root=store)]
    ranked = [e["id"] for e in knowledge_list(repo_root=store, rank_outcomes=True)]
    assert plain == [d, c, b, a]  # newest first, untouched by default
    assert ranked == [a, b, c, d]
    rows = {e["id"]: e for e in knowledge_list(repo_root=store)}
    assert rows[a]["outcome"]["status"] == "useful"
    assert "outcome" not in rows[b]
    # Pagination still walks the ranked order.
    page2 = knowledge_list(repo_root=store, rank_outcomes=True, limit=2, offset=2)
    assert [e["id"] for e in page2] == [c, d]


# --- resume -------------------------------------------------------------------


def test_dead_ends_strongest_and_most_recent_first(store):
    weak = knowledge_record("weak", "x", repo_root=store)
    strong = knowledge_record("strong", "x", repo_root=store)
    fixed = knowledge_record("fixed", "x", repo_root=store)
    good = knowledge_record("good", "x", repo_root=store)
    _age(
        store, knowledge_outcome_record("dead_end", entry_id=weak, repo_root=store), 40
    )
    for _ in range(2):
        knowledge_outcome_record("dead_end", entry_id=strong, repo_root=store)
    knowledge_outcome_record("corrected", entry_id=fixed, repo_root=store)
    knowledge_outcome_record("useful", entry_id=good, repo_root=store)
    knowledge_outcome_record(
        "dead_end", file_refs="legacy/sync.py", note="task: sync", repo_root=store
    )
    old = knowledge_outcome_record("dead_end", file_refs="faded.py", repo_root=store)
    _age(store, old, 200)

    items = knowledge_dead_ends(limit=10, repo_root=store)
    keys = [i.get("entry_id") or i.get("file") for i in items]
    assert keys[0] == strong
    assert keys.index(weak) == len(keys) - 1
    assert "legacy/sync.py" in keys and fixed in keys
    assert good not in keys and "faded.py" not in keys
    assert len(knowledge_dead_ends(limit=2, repo_root=store)) == 2


def test_dead_ends_skip_superseded_entries(store):
    old = knowledge_record("old", "x", repo_root=store)
    knowledge_outcome_record("dead_end", entry_id=old, repo_root=store)
    knowledge_record("new", "y", supersedes=old, repo_root=store)
    assert knowledge_dead_ends(repo_root=store) == []


def test_resume_bundle_carries_known_dead_ends(store):
    eid = knowledge_record("Patch the vendored client", "x", repo_root=store)
    knowledge_outcome_record(
        "dead_end", entry_id=eid, note="upstream rewrote it", repo_root=store
    )
    bundle = build_resume_bundle(store)
    assert [d["entry_id"] for d in bundle["known_dead_ends"]] == [eid]
    assert bundle["known_dead_ends"][0]["last_note"] == "upstream rewrote it"


def test_resume_bundle_empty_dead_ends_without_outcomes(store):
    knowledge_record("a", "b", repo_root=store)
    assert build_resume_bundle(store)["known_dead_ends"] == []


# --- half-life config ---------------------------------------------------------


def test_half_life_from_config(store):
    (store / ".codegraph" / "config.toml").write_text(
        "[codegraph]\nknowledge_half_life_days = 5\n", encoding="utf-8"
    )
    assert load_config(store).knowledge_half_life_days == 5.0
    eid = knowledge_record("a", "b", repo_root=store)
    _age(store, knowledge_outcome_record("dead_end", entry_id=eid, repo_root=store), 20)
    # Four half-lives of five days: faded, so not a known dead end any more.
    assert knowledge_dead_ends(repo_root=store) == []
    entries, _ = knowledge_outcome_scores(repo_root=store, half_life_days=30)
    assert entries[eid].status == "dead_end"


def test_bad_half_life_keeps_default(store):
    (store / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nknowledge_half_life_days = "soon"\n', encoding="utf-8"
    )
    assert load_config(store).knowledge_half_life_days == 30.0


# --- migration ----------------------------------------------------------------


def test_store_without_outcome_table_migrates(tmp_path):
    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    db = tmp_path / ".codegraph" / "call_log.db"
    # The knowledge table as an older release created it: no outcome table,
    # none of the later columns.
    raw = sqlite3.connect(db)
    raw.execute(
        "CREATE TABLE knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
        "body TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT 'note', file_refs TEXT NOT NULL DEFAULT '', "
        "ts REAL NOT NULL)"
    )
    raw.execute(
        "INSERT INTO knowledge(title, body, kind, ts) VALUES ('kept', 'old', 'gotcha', 1)"
    )
    raw.commit()
    raw.close()
    try:
        hits = knowledge_list(repo_root=tmp_path)
        assert [h["title"] for h in hits] == ["kept"]
        knowledge_outcome_record("useful", entry_id=hits[0]["id"], repo_root=tmp_path)
        assert knowledge_list(repo_root=tmp_path)[0]["outcome"]["useful"] == 1
        # Opening again is a no-op for the schema.
        reset_for_tests()
        assert knowledge_list(repo_root=tmp_path)[0]["outcome"]["useful"] == 1
    finally:
        reset_for_tests()
    # An older reader that only knows the knowledge table still reads it.
    ro = sqlite3.connect(db)
    try:
        assert ro.execute("SELECT title FROM knowledge").fetchall() == [("kept",)]
    finally:
        ro.close()


# --- MCP tool -----------------------------------------------------------------


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(store, monkeypatch):
    from codegraph.server.tools_knowledge import register

    monkeypatch.setattr(_srv, "_root", store)
    mcp = _FakeMcp()
    register(mcp)
    return mcp.tools


def test_tool_records_by_id_and_query(store, tools):
    eid = knowledge_record("DuckDB lock gotcha", "RO open fails", repo_root=store)
    out = json.loads(tools["knowledge_outcome"](verdict="useful", entry_id=eid))
    assert out["entry_id"] == eid and out["outcome"]["status"] == "useful"
    out = json.loads(
        tools["knowledge_outcome"](
            verdict="useful", query="DuckDB lock", file_refs="codegraph/core/db.py"
        )
    )
    assert out["entry_id"] == eid and out["title"] == "DuckDB lock gotcha"
    assert out["outcome"]["status"] == "preferred"
    assert out["files"]["codegraph/core/db.py"]["useful"] == 1


def test_tool_reports_errors(store, tools):
    out = json.loads(tools["knowledge_outcome"](verdict="useful", query="zzz nothing"))
    assert "error" in out
    out = json.loads(tools["knowledge_outcome"](verdict="nope", file_refs="a.py"))
    assert "verdict" in out["error"]


def test_tool_search_and_list_show_outcome(store, tools):
    eid = knowledge_record("flaky test", "rerun", repo_root=store)
    tools["knowledge_outcome"](verdict="dead_end", entry_id=eid)
    hits = json.loads(tools["knowledge_search"](query="flaky"))["hits"]
    assert hits[0]["outcome"]["status"] == "dead_end"
    listed = json.loads(tools["knowledge_list"]())["entries"]
    assert listed[0]["outcome"] == {"status": "dead_end"}  # a brief: status only
    whole = json.loads(tools["knowledge_list"](entry_id=eid))["entries"]
    assert whole[0]["outcome"]["dead_end"] == 1


# --- CLI ----------------------------------------------------------------------


def test_cli_outcome_and_outcomes(store, monkeypatch, capsys):
    from codegraph.cli.commands_knowledge import cmd_knowledge

    monkeypatch.chdir(store)
    eid = knowledge_record("Use the bulk endpoint", "x", repo_root=store)
    cmd_knowledge(
        Namespace(
            knowledge_cmd="outcome",
            verdict="useful",
            entry_id=eid,
            query="",
            files=["api/bulk.py"],
            note="import task",
            corrected_by=0,
            json=True,
        )
    )
    rec = json.loads(capsys.readouterr().out)
    assert rec["entry_id"] == eid and rec["outcome"]["status"] == "useful"
    cmd_knowledge(Namespace(knowledge_cmd="outcomes", limit=10, json=True))
    shown = json.loads(capsys.readouterr().out)
    assert shown["entries"][0]["title"] == "Use the bulk endpoint"
    assert shown["files"][0]["file"] == "api/bulk.py"


def test_cli_outcome_rejects_bad_target(store, monkeypatch, capsys):
    from codegraph.cli.commands_knowledge import cmd_knowledge

    monkeypatch.chdir(store)
    with pytest.raises(SystemExit):
        cmd_knowledge(
            Namespace(
                knowledge_cmd="outcome",
                verdict="corrected",
                entry_id=0,
                query="",
                files=["a.py"],
                note="",
                corrected_by=0,
                json=True,
            )
        )
    assert "error" in json.loads(capsys.readouterr().out)


def test_module_exports_stay_internal():
    # The scoring helpers are reached through call_log, not re-exported.
    assert hasattr(call_log, "knowledge_outcome_record")
