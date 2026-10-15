# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The short resume of a session that wrote a checkpoint: its
#              checkpoint, the standing instructions, the dead ends and a few
#              task-matched entries, and the fall back to the full bundle.

from __future__ import annotations

import json

import pytest

from codegraph.server.tools_session import session_resume
from codegraph.state import call_log
from codegraph.state.call_log import knowledge_record


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    call_log.reset_for_tests()
    knowledge_record(
        "Use the commit skill", "Never raw git commit.",
        kind="standing_instruction", repo_root=tmp_path,
    )  # fmt: skip
    for i in range(20):
        knowledge_record(
            f"Session digest other-{i}", "z" * 2000,
            tags="compaction,session-digest", session_id=f"other-{i}",
            repo_root=tmp_path,
        )  # fmt: skip
    knowledge_record(
        "DuckDB lock gotcha", "RO opens blocked while owner alive.",
        kind="gotcha", repo_root=tmp_path,
    )  # fmt: skip
    yield tmp_path
    call_log.reset_for_tests()


def _checkpoint(repo, sid: str, body: str) -> None:
    knowledge_record(
        f"Session digest {sid}", body, tags="compaction,session-digest",
        session_id=sid, repo_root=repo,
    )  # fmt: skip


def test_session_with_a_checkpoint_gets_the_short_bundle(repo):
    _checkpoint(repo, "s1", "Done: the migration. Next: the portal filter.")
    out = session_resume(repo, "s1", task="DuckDB lock")
    assert out["mode"] == "session"
    assert out["checkpoint"]["body"].startswith("Done: the migration")
    assert [r["title"] for r in out["standing_instructions"]] == [
        "Use the commit skill"
    ]
    assert [k["title"] for k in out["knowledge"]] == ["DuckDB lock gotcha"]
    assert "continue from its next step" in out["next"]
    # No other session's digest rides along: the answer stays small.
    assert "other-" not in json.dumps(out)
    assert len(json.dumps(out, default=str)) < 3_000


def test_latest_checkpoint_wins(repo):
    _checkpoint(repo, "s2", "v1")
    _checkpoint(repo, "s2", "v2")
    assert session_resume(repo, "s2")["checkpoint"]["body"] == "v2"


def test_no_task_means_no_knowledge(repo):
    _checkpoint(repo, "s3", "state")
    assert session_resume(repo, "s3")["knowledge"] == []


def test_session_without_a_checkpoint_falls_back(repo):
    assert session_resume(repo, "unknown") is None
    assert session_resume(repo, "") is None


def test_auto_marker_alone_is_not_a_checkpoint(repo):
    knowledge_record(
        "Auto checkpoint", "details are gone",
        tags="auto-checkpoint,session-digest", session_id="s4", repo_root=repo,
    )  # fmt: skip
    assert session_resume(repo, "s4") is None
