# Copyright (c) 2026 ALTIKVA. All rights reserved.
# SPDX-License-Identifier: MIT AND CC-BY-NC-SA-4.0
#
# Project:     cgh (codegraph)
# Description: A repo with no git HEAD still gets scanned. Reporting "no
#              scan recorded" there sends the reader to re-run an index
#              that changes nothing.
# Author:      jndjama (Joy Ndjama)

from datetime import UTC, datetime

from codegraph.cli.commands_monitor import _scan_line

NOW = datetime.now(UTC).isoformat(timespec="seconds")


class TestScanLine:
    def test_fresh(self):
        line = _scan_line(
            {"indexed_sha": "abc1234", "indexed_at": NOW, "current_sha": "abc1234"},
            {"fresh": True, "indexed_branch": "main"},
        )
        assert "fresh" in line and "abc1234" in line

    def test_stale_reports_the_drift(self):
        line = _scan_line(
            {"indexed_sha": "abc1234", "indexed_at": NOW, "current_sha": "def5678"},
            {"fresh": False, "behind_by": 3, "dirty": True},
        )
        assert "stale" in line
        assert "3 commits behind" in line and "working tree dirty" in line

    def test_a_scanned_repo_without_git_is_not_called_unscanned(self):
        """A federation parent is typically not a git repository at all."""
        line = _scan_line(
            {"indexed_sha": None, "indexed_at": NOW, "current_sha": None},
            {"fresh": False},
        )
        assert "no scan recorded" not in line
        assert "not a git repository" in line

    def test_a_scan_with_git_but_no_recorded_head(self):
        line = _scan_line(
            {"indexed_sha": None, "indexed_at": NOW, "current_sha": "def5678"},
            {"fresh": False},
        )
        assert "no scan recorded" not in line
        assert "no recorded HEAD" in line

    def test_never_scanned_still_says_so(self):
        line = _scan_line(
            {"indexed_sha": None, "indexed_at": None, "current_sha": "def5678"},
            {"fresh": False},
        )
        assert "no scan recorded" in line
