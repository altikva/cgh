# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh knowledge export` / `import`: time, commit, tag-glob and kind
#              filters (digests and checkpoints included), a lossless round
#              trip, FTS on imported rows, and the refusal while an owner runs.

from __future__ import annotations

import io
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from codegraph.cli.commands_knowledge import cmd_knowledge
from codegraph.state import ipc
from codegraph.state.call_log import (
    _get_conn,
    knowledge_outcome_record,
    knowledge_record,
    knowledge_search,
    reset_for_tests,
)
from codegraph.state.knowledge_transfer import (
    commit_time,
    export_records,
    import_records,
    parse_time,
)


def _store(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".codegraph").mkdir(parents=True)
    return root


def _set_ts(root: Path, entry_id: int, ts: float) -> None:
    conn = _get_conn(root)
    conn.execute("UPDATE knowledge SET ts = ? WHERE id = ?", (ts, entry_id))
    conn.commit()


@pytest.fixture
def src(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "main")
    old = knowledge_record("Old gotcha", "lock the row", kind="gotcha",
                           tags="db, locking", repo_root=root)  # fmt: skip
    new = knowledge_record("New decision", "use a queue", kind="decision",
                           tags="queue", repo_root=root)  # fmt: skip
    promoted = knowledge_record(
        "Promoted pattern", "the fix itself", kind="pattern", tags="x",
        repo_root=root, source_pr="ondonne-api#2488", source_branch="fix/2488",
    )  # fmt: skip
    old_digest = knowledge_record(
        "Session digest a", "early session", kind="note",
        tags="compaction,session-digest", session_id="a", repo_root=root,
    )  # fmt: skip
    new_digest = knowledge_record(
        "Session digest b", "line by line solution", kind="note",
        tags="compaction,session-digest", session_id="b", repo_root=root,
    )  # fmt: skip
    for eid, ts in ((old, 1000.0), (new, 3000.0), (promoted, 1500.0),
                    (old_digest, 1100.0), (new_digest, 3100.0)):  # fmt: skip
        _set_ts(root, eid, ts)
    yield root
    reset_for_tests()


def _titles(records):
    return sorted(r["title"] for r in records if r["record"] == "knowledge")


def test_filter_by_time(src):
    recs = export_records(src, before=2000.0)
    assert _titles(recs) == [
        "Old gotcha", "Promoted pattern", "Session digest a",
    ]  # fmt: skip
    # strictly before
    assert "Old gotcha" not in _titles(export_records(src, before=1000.0))


def test_parse_time_accepts_epoch_and_iso():
    assert parse_time("1700000000") == 1700000000.0
    assert parse_time("1970-01-01T00:16:40Z") == 1000.0
    assert parse_time("1970-01-01") == 0.0


def test_filter_by_commit(src, tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()
    env = {
        "GIT_COMMITTER_DATE": "@2000 +0000",
        "GIT_AUTHOR_DATE": "@2000 +0000",
        "HOME": str(tmp_path),
        "PATH": __import__("os").environ["PATH"],
    }
    for cmd in (["init", "-q"],
                ["-c", "user.name=t", "-c", "user.email=t@t",
                 "commit", "-q", "--allow-empty", "-m", "parent"]):  # fmt: skip
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, env=env)
    assert commit_time(repo, "HEAD") == 2000.0
    recs = export_records(src, before=commit_time(repo, "HEAD"))
    assert "New decision" not in _titles(recs)
    assert "Old gotcha" in _titles(recs)


def test_filter_by_tag_glob_and_provenance(src):
    recs = export_records(src, exclude_tags=["lock*"])
    assert "Old gotcha" not in _titles(recs)
    recs = export_records(src, exclude_tags=["pr:*#2488"])
    assert "Promoted pattern" not in _titles(recs)
    recs = export_records(src, exclude_tags=["branch:fix/*", "queue"])
    assert "Promoted pattern" not in _titles(recs)
    assert "New decision" not in _titles(recs)
    # a glob matches whole tags, not substrings
    assert "Old gotcha" in _titles(export_records(src, exclude_tags=["lock"]))


def test_filter_by_kinds(src):
    recs = export_records(src, kinds=["gotcha", "decision"])
    assert _titles(recs) == ["New decision", "Old gotcha"]


def test_digests_follow_the_filters(src):
    recs = export_records(src, before=2000.0)
    assert "Session digest a" in _titles(recs)
    assert "Session digest b" not in _titles(recs)
    recs = export_records(src, exclude_tags=["session-digest"])
    assert not [t for t in _titles(recs) if t.startswith("Session digest")]
    recs = export_records(src, include_digests=False)
    assert not [t for t in _titles(recs) if t.startswith("Session digest")]
    assert "Old gotcha" in _titles(recs)


def test_round_trip_is_lossless(src, tmp_path):
    first = knowledge_record("v1", "first take", kind="decision", repo_root=src)
    knowledge_record("v2", "second take", kind="decision", repo_root=src,
                     supersedes=first)  # fmt: skip
    knowledge_outcome_record("useful", entry_id=first, note="ok", repo_root=src)
    knowledge_outcome_record("dead_end", file_refs=["a.py"], repo_root=src)
    dst = _store(tmp_path, "wt")
    recs = export_records(src)
    counts = import_records(dst, recs)
    assert counts["imported"] == len([r for r in recs if r["record"] == "knowledge"])
    assert counts["outcomes_imported"] == 2

    def _strip(rows, refs):
        by_id = {r["id"]: r for r in rows if r["record"] == "knowledge"}
        out = []
        for r in rows:
            r = dict(r)
            for ref in refs:
                if r.get(ref) is not None:
                    r[ref] = by_id[r[ref]]["title"]
            r.pop("id")
            out.append(r)
        return sorted(out, key=lambda r: json.dumps(r, sort_keys=True))

    again = export_records(dst)
    refs = ("superseded_by", "entry_id", "corrected_by")
    assert _strip(again, refs) == _strip(recs, refs)
    # importing twice adds nothing
    counts = import_records(dst, recs)
    assert counts["imported"] == 0 and counts["outcomes_imported"] == 0


def test_supersede_link_to_a_filtered_row_is_cleared(src):
    first = knowledge_record("v1", "first", kind="decision", repo_root=src)
    second = knowledge_record("v2", "second", kind="decision", repo_root=src,
                              supersedes=first)  # fmt: skip
    _set_ts(src, first, 500.0)
    _set_ts(src, second, 5000.0)
    recs = {r["title"]: r for r in export_records(src, before=2000.0)}
    assert recs["v1"]["superseded_by"] is None


def test_fts_finds_imported_entries(src, tmp_path):
    dst = _store(tmp_path, "wt")
    import_records(dst, export_records(src, before=2000.0))
    hits = knowledge_search("row", repo_root=dst)
    assert [h["title"] for h in hits] == ["Old gotcha"]
    assert knowledge_search("queue", repo_root=dst) == []


def _cli_import(root: Path, payload: str, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    cmd_knowledge(Namespace(knowledge_cmd="import", root=str(root), file="-",
                            json=True))  # fmt: skip


def test_cli_export_import_pipeline(src, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)
    cmd_knowledge(
        Namespace(knowledge_cmd="export", root=str(src), before="2000",
                  before_commit="", exclude_tags=["pr:*#2488"], kinds="",
                  include_digests=True)
    )  # fmt: skip
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines()]
    assert _titles(lines) == ["Old gotcha", "Session digest a"]
    dst = _store(tmp_path, "wt")
    _cli_import(dst, out, monkeypatch, capsys)
    assert json.loads(capsys.readouterr().out)["imported"] == 2


def test_import_refuses_while_owner_alive(src, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: True)
    monkeypatch.setattr(ipc, "read_owner_pid", lambda root: 4242)
    dst = _store(tmp_path, "wt")
    with pytest.raises(SystemExit) as exc:
        _cli_import(dst, json.dumps(export_records(src)[0]) + "\n",
                    monkeypatch, capsys)  # fmt: skip
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "owner is running" in err and "cgh stop --root" in err
    assert not (dst / ".codegraph" / "call_log.db").exists()
