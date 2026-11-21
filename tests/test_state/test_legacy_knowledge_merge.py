# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A repo whose .cgh/ was created beside its old .codegraph/ (so the
#              rename never ran) gets the legacy knowledge merged into .cgh/
#              once: duplicates skipped, outcomes carried, legacy dir kept, and
#              the marker stops a second pass unless forced.

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from cgh.cli.commands_knowledge import cmd_knowledge
from cgh.core import config
from cgh.state.call_log import (
    knowledge_outcome_record,
    knowledge_record,
    knowledge_search,
    reset_for_tests,
)
from cgh.state.knowledge_transfer import LEGACY_MERGE_MARKER, merge_legacy_knowledge


@pytest.fixture
def repo(tmp_path: Path):
    """Both dirs: .codegraph/ with two entries and an outcome, .cgh/ with one
    entry that duplicates a legacy one."""
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    gotcha = knowledge_record(
        "Old gotcha", "lock the row", kind="gotcha", repo_root=root
    )
    knowledge_record("Old decision", "use a queue", kind="decision", repo_root=root)
    knowledge_outcome_record("useful", entry_id=gotcha, note="held", repo_root=root)
    reset_for_tests()
    (root / ".cgh").rename(root / ".codegraph")
    (root / ".cgh").mkdir()
    knowledge_record("Old decision", "use a queue", kind="decision", repo_root=root)
    knowledge_record("New note", "after the switch", kind="note", repo_root=root)
    yield root
    reset_for_tests()


def test_merge_imports_legacy_rows_and_skips_duplicates(repo):
    counts = merge_legacy_knowledge(repo)

    assert counts is not None
    assert counts["imported"] == 1
    assert counts["skipped_duplicate"] == 1
    assert counts["outcomes_imported"] == 1
    hits = knowledge_search("lock the row", repo_root=repo)
    assert [h["title"] for h in hits] == ["Old gotcha"]
    # The legacy dir is left alone: nothing is deleted.
    assert (repo / ".codegraph" / "call_log.db").is_file()


def test_marker_stops_a_second_pass_unless_forced(repo):
    assert merge_legacy_knowledge(repo) is not None
    marker = json.loads((repo / ".cgh" / LEGACY_MERGE_MARKER).read_text())
    assert marker["imported"] == 1

    assert merge_legacy_knowledge(repo) is None
    again = merge_legacy_knowledge(repo, force=True)
    assert again is not None and again["imported"] == 0


def test_migrate_state_dir_merges_when_both_dirs_exist(repo):
    assert config.migrate_state_dir(repo) is False
    assert (repo / ".cgh" / LEGACY_MERGE_MARKER).exists()
    assert knowledge_search("lock the row", repo_root=repo)


def test_nothing_to_merge_without_a_legacy_store(tmp_path):
    reset_for_tests()
    root = tmp_path / "fresh"
    (root / ".cgh").mkdir(parents=True)
    (root / ".codegraph").mkdir()  # a debris dir: no call_log.db in it
    assert merge_legacy_knowledge(root) is None
    assert config.migrate_state_dir(root) is False
    assert not (root / ".cgh" / LEGACY_MERGE_MARKER).exists()


def test_cli_merge_legacy_reports_json(repo, capsys):
    cmd_knowledge(
        Namespace(knowledge_cmd="merge-legacy", root=str(repo), force=False, json=True)
    )
    out = json.loads(capsys.readouterr().out)
    assert out["merged"] is True
    assert out["imported"] == 1
