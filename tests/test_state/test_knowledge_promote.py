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
