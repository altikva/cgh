# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-20
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for digest tags and knowledge count/list consistency.

from __future__ import annotations


def test_digest_tags_always_carries_the_marker():
    from codegraph.server.tools_knowledge import _digest_tags

    assert _digest_tags("") == "compaction,session-digest"
    assert _digest_tags("cards,ondonne") == "cards,ondonne,session-digest"
    assert _digest_tags("x,session-digest") == "x,session-digest"


def test_count_matches_list_excluding_superseded(tmp_path):
    from codegraph.state.call_log import (
        knowledge_count,
        knowledge_list,
        knowledge_record,
        reset_for_tests,
    )

    reset_for_tests()
    (tmp_path / ".codegraph").mkdir()
    old = knowledge_record("old", "o", kind="note", tags="x", repo_root=tmp_path)
    knowledge_record(
        "new", "n", kind="note", tags="x", repo_root=tmp_path, supersedes=old
    )
    knowledge_record("other", "z", kind="note", tags="y", repo_root=tmp_path)
    count = knowledge_count(repo_root=tmp_path)
    listed = knowledge_list(limit=100, repo_root=tmp_path)
    assert count == len(listed)
    reset_for_tests()
