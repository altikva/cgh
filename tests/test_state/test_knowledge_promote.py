# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Knowledge promotion between stores (a per-ticket worktree into its
#              main checkout), the exclude_tag filter it relies on, and the
#              doctor --strict exit code.

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from codegraph.state.call_log import (
    _get_conn,
    knowledge_list,
    knowledge_record,
    promote_knowledge,
    reset_for_tests,
)


def _store(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".codegraph").mkdir(parents=True)
    return root


def test_promote_carries_real_learnings_only(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record(
        "Lock the counter",
        "take the row lock, do not derive from count",
        kind="gotcha",
        repo_root=src,
    )
    knowledge_record(
        "branch-only decision", "temp on my branch", kind="decision",
        repo_root=src, scope="branch",
    )  # fmt: skip
    knowledge_record(
        "Auto checkpoint", "details gone", kind="note",
        tags="auto-checkpoint,session-digest", repo_root=src,
    )  # fmt: skip

    result = promote_knowledge(src, dst, source_branch="feature/x")
    assert result["promoted"] == 1
    titles = [e["title"] for e in knowledge_list(repo_root=dst)]
    assert titles == ["Lock the counter"]  # branch-scoped and digest excluded


def test_promote_is_idempotent(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("A decision", "chose X over Y", kind="decision", repo_root=src)

    first = promote_knowledge(src, dst)
    second = promote_knowledge(src, dst)
    assert first["promoted"] == 1
    assert second["promoted"] == 0 and second["skipped_duplicate"] == 1
    assert len(knowledge_list(repo_root=dst)) == 1  # no duplicate row


def test_promote_preserves_origin_ts_and_provenance(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("Gotcha", "watch out for Z", kind="gotcha", repo_root=src)
    origin_ts = knowledge_list(repo_root=src)[0]["ts"]

    promote_knowledge(
        src, dst, source_branch="feature/z", source_commit="abc123",
        source_pr="ondonne-api#42", source_session="sess-1",
    )  # fmt: skip
    row = (
        _get_conn(dst)
        .execute(
            "SELECT source_branch, source_commit, source_pr, source_session, "
            "origin_ts, promoted_at FROM knowledge WHERE title = 'Gotcha'"
        )
        .fetchone()
    )
    assert row[0] == "feature/z"
    assert row[2] == "ondonne-api#42"
    assert row[4] == pytest.approx(origin_ts)  # origin ts preserved
    assert row[5] is not None and row[5] >= origin_ts  # promoted_at is set, later


def test_knowledge_list_exclude_tag(tmp_path):
    reset_for_tests()
    root = _store(tmp_path, "r")
    knowledge_record("keep", "real", kind="gotcha", tags="owner", repo_root=root)
    knowledge_record(
        "drop", "digest", kind="note", tags="auto-checkpoint,session-digest",
        repo_root=root,
    )  # fmt: skip
    titles = [
        e["title"] for e in knowledge_list(exclude_tag="session-digest", repo_root=root)
    ]
    assert titles == ["keep"]


def test_doctor_strict_exits_nonzero_when_unconfigured(tmp_path):
    from codegraph.cli.commands_monitor import cmd_doctor

    with pytest.raises(SystemExit) as exc:
        cmd_doctor(Namespace(root=str(tmp_path), strict=True))
    assert exc.value.code == 1


def test_doctor_without_strict_does_not_exit(tmp_path):
    from codegraph.cli.commands_monitor import cmd_doctor

    # No SystemExit even though the bare dir fails checks.
    cmd_doctor(Namespace(root=str(tmp_path), strict=False))


# ---------------------------------------------------------------------------
# Origin-aware promotion: migration, supersede, dry run, archive, CLI,
# concurrent writers on the target.
# ---------------------------------------------------------------------------


def _promote_args(src: Path, dst: Path, **kw) -> Namespace:
    base = dict(
        knowledge_cmd="promote", from_=str(src), to=str(dst), since=0.0, kinds="",
        no_notes=False, pr="", branch="feature/t", commit="", session="",
        archive="", dry_run=False, json=True,
    )  # fmt: skip
    base.update(kw)
    return Namespace(**base)


def test_old_schema_store_migrates_silently(tmp_path):
    import sqlite3

    reset_for_tests()
    root = _store(tmp_path, "old")
    # A store as cgh 0.13 left it: no superseded_by, no provenance columns.
    conn = sqlite3.connect(root / ".codegraph" / "call_log.db")
    conn.execute(
        "CREATE TABLE knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
        "body TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT 'note', file_refs TEXT NOT NULL DEFAULT '', "
        "ts REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO knowledge(title, body, kind, ts) VALUES ('old', 'kept', 'gotcha', 1)"
    )
    conn.commit()
    conn.close()

    entries = knowledge_list(repo_root=root)
    assert [e["title"] for e in entries] == ["old"]
    assert "provenance" not in entries[0]  # native entry keeps its old shape
    cols = {r[1] for r in _get_conn(root).execute("PRAGMA table_info(knowledge)")}
    assert {"source_worktree", "origin_id", "promoted_at", "superseded_by"} <= cols


def test_promote_skips_digests_checkpoints_and_superseded(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("rule", "always X", kind="standing_instruction", repo_root=src)
    knowledge_record("a note", "plain", kind="note", repo_root=src)
    knowledge_record("cp", "d", kind="note", tags="compaction,session-digest",
                     repo_root=src)  # fmt: skip
    knowledge_record("bob", "d", kind="note", tags="auto-digest,bob", repo_root=src)
    old = knowledge_record("v1", "first", kind="decision", repo_root=src)
    knowledge_record("v2", "second", kind="decision", repo_root=src, supersedes=old)

    result = promote_knowledge(src, dst)
    titles = sorted(e["title"] for e in knowledge_list(repo_root=dst))
    assert titles == ["a note", "rule", "v2"]
    assert result["copied"] == 3 and result["superseded"] == 0


def test_explicit_kinds_are_exact(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("g", "b", kind="gotcha", repo_root=src)
    knowledge_record("n", "b", kind="note", repo_root=src)
    promote_knowledge(src, dst, kinds=["gotcha"])
    assert [e["title"] for e in knowledge_list(repo_root=dst)] == ["g"]


def test_rerun_does_not_resurrect_an_entry_the_target_replaced(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("Pin", "pin to 1.2", kind="decision", repo_root=src)
    promote_knowledge(src, dst, source_branch="f/a")
    copy_id = knowledge_list(repo_root=dst)[0]["id"]
    knowledge_record("Pin", "pin to 1.3", kind="decision", repo_root=dst,
                     supersedes=copy_id)  # fmt: skip

    again = promote_knowledge(src, dst, source_branch="f/a")
    assert again["promoted"] == 0 and again["skipped_duplicate"] == 1
    assert [e["body"] for e in knowledge_list(repo_root=dst)] == ["pin to 1.3"]


def test_revised_entry_supersedes_its_earlier_copy(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    first = knowledge_record("Retry", "3 tries", kind="pattern", repo_root=src)
    promote_knowledge(src, dst, source_branch="f/b")
    old_copy = knowledge_list(repo_root=dst)[0]["id"]

    # The agent revises the entry on the branch, then the merge promotes again.
    knowledge_record("Retry", "5 tries with backoff", kind="pattern",
                     repo_root=src, supersedes=first)  # fmt: skip
    result = promote_knowledge(src, dst, source_branch="f/b")
    assert result["superseded"] == 1 and result["copied"] == 0
    live = knowledge_list(repo_root=dst)
    assert [e["body"] for e in live] == ["5 tries with backoff"]
    sup = (
        _get_conn(dst)
        .execute("SELECT superseded_by FROM knowledge WHERE id = ?", (old_copy,))
        .fetchone()[0]
    )
    assert sup == live[0]["id"]


def test_revision_of_a_seeded_entry_supersedes_the_main_original(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    # init --from seeds the worktree with main's entries; mimic that.
    knowledge_record("Port", "8000", kind="gotcha", repo_root=dst)
    seeded = knowledge_record("Port", "8000", kind="gotcha", repo_root=src)
    knowledge_record("Port", "8080 since the proxy", kind="gotcha", repo_root=src,
                     supersedes=seeded)  # fmt: skip

    result = promote_knowledge(src, dst)
    assert result["superseded"] == 1
    assert [e["body"] for e in knowledge_list(repo_root=dst)] == [
        "8080 since the proxy"
    ]


def test_provenance_columns_and_read_side(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    sid = knowledge_record("Cache key", "include tenant", kind="gotcha", repo_root=src)
    promote_knowledge(src, dst, source_branch="f/c", source_pr="ondonne-api#7")

    from codegraph.state.call_log import knowledge_search

    for entry in (
        knowledge_list(repo_root=dst)[0],
        knowledge_search("tenant", repo_root=dst)[0],
    ):
        prov = entry["provenance"]
        assert prov["source_branch"] == "f/c"
        assert prov["source_pr"] == "ondonne-api#7"
        assert prov["source_worktree"] == str(src.resolve())
        assert prov["origin_id"] == sid
        assert prov["promoted_at"] > 0
    knowledge_record("native", "x", kind="note", repo_root=dst)
    native = [e for e in knowledge_list(repo_root=dst) if e["title"] == "native"]
    assert "provenance" not in native[0]


def test_dry_run_writes_nothing(tmp_path):
    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("one", "a", kind="gotcha", repo_root=src)
    knowledge_record("two", "b", kind="decision", repo_root=src)
    result = promote_knowledge(src, dst, dry_run=True)
    assert result["copied"] == 2 and result["dry_run"] is True
    assert knowledge_list(repo_root=dst) == []


def test_cli_archive_json_and_rerun(tmp_path, capsys):
    import json

    from codegraph.cli.commands_knowledge import cmd_knowledge

    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_record("keep", "real", kind="gotcha", repo_root=src)
    knowledge_record("cp", "d", kind="note", tags="auto-checkpoint,session-digest",
                     repo_root=src)  # fmt: skip
    archive = tmp_path / "archive"

    cmd_knowledge(_promote_args(src, dst, archive=str(archive), pr="api#9"))
    out = json.loads(capsys.readouterr().out)
    assert out["copied"] == 1 and out["pr"] == "api#9"
    lines = Path(out["archive"]["path"]).read_text().splitlines()
    assert len(lines) == 2  # the archive keeps the checkpoint too
    assert {json.loads(x)["title"] for x in lines} == {"keep", "cp"}

    cmd_knowledge(_promote_args(src, dst))
    again = json.loads(capsys.readouterr().out)
    assert again["promoted"] == 0 and again["skipped_duplicate"] == 1


def test_cli_rejects_same_store(tmp_path, capsys):
    from codegraph.cli.commands_knowledge import cmd_knowledge

    reset_for_tests()
    root = _store(tmp_path, "r")
    knowledge_record("x", "y", kind="gotcha", repo_root=root)
    with pytest.raises(SystemExit):
        cmd_knowledge(_promote_args(root, root))


_WRITER = """
import sqlite3, sys, time
db, n = sys.argv[1], int(sys.argv[2])
conn = sqlite3.connect(db, timeout=30)
for i in range(n):
    # Hold the write lock a moment each time, like an owner mid-write.
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO knowledge(title, body, kind, ts) VALUES (?, ?, 'note', ?)",
        (f"owner-{i}", "concurrent", time.time()),
    )
    time.sleep(0.01)
    conn.commit()
    if i == 0:
        print("ready", flush=True)  # promote starts only once writes are flowing
    time.sleep(0.01)  # an owner writes in bursts, not in a tight loop
conn.close()
"""


def test_promote_while_another_process_writes_the_target(tmp_path):
    import subprocess
    import sys

    reset_for_tests()
    src = _store(tmp_path, "wt")
    dst = _store(tmp_path, "main")
    knowledge_list(repo_root=dst)  # create the target schema (the owner's store)
    for i in range(60):
        knowledge_record(f"learned {i}", f"body {i}", kind="gotcha", repo_root=src)

    writer = subprocess.Popen(
        [sys.executable, "-c", _WRITER, str(dst / ".codegraph" / "call_log.db"), "80"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert writer.stdout is not None and writer.stdout.readline().strip() == "ready"
    try:
        result = promote_knowledge(src, dst, source_branch="f/live")
    finally:
        assert writer.wait(timeout=60) == 0
    assert result["copied"] == 60
    titles = {e["title"] for e in knowledge_list(repo_root=dst, limit=500)}
    assert {f"learned {i}" for i in range(60)} <= titles
    assert {f"owner-{i}" for i in range(80)} <= titles
    conn = _get_conn(dst)
    owner_ids = [
        r[0] for r in conn.execute("SELECT id FROM knowledge WHERE body = 'concurrent'")
    ]
    promoted_ids = [
        r[0]
        for r in conn.execute("SELECT id FROM knowledge WHERE origin_id IS NOT NULL")
    ]
    # The two writers really interleaved: owner rows land inside the promote run.
    assert min(promoted_ids) < max(owner_ids) and min(owner_ids) < max(promoted_ids)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
