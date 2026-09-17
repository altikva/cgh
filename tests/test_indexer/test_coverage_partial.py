# Copyright (c) 2026 ALTIKVA. All rights reserved.
# SPDX-License-Identifier: MIT AND CC-BY-NC-SA-4.0
#
# Project:     cgh (codegraph)
# Description: A scan that short-circuits unchanged files measures only what
#              it parsed. Letting that smaller number land would replace a
#              full measurement with a partial one, silently.
# Author:      jndjama (Joy Ndjama)

import json

from codegraph.cli.commands_monitor import _imports_coverage_line
from codegraph.state.scan_meta import _kept_stats

FULL = {"python": {"seen": 217, "resolved": 46}}
PARTIAL = {"python": {"seen": 190, "resolved": 31}}


def _repo_with_meta(tmp_path, stats):
    cg = tmp_path / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    (cg / "scan_meta.json").write_text(json.dumps({"stats": stats}))
    return tmp_path


class TestPartialCoverageDoesNotLand:
    def test_a_partial_run_keeps_the_full_measurement(self, tmp_path):
        root = _repo_with_meta(tmp_path, {"imports": FULL, "imports_partial": False})

        kept = _kept_stats(root, {"imports": PARTIAL, "imports_partial": True})

        assert kept["imports"] == FULL
        assert kept["imports_partial"] is False

    def test_a_full_run_replaces_a_partial_measurement(self, tmp_path):
        root = _repo_with_meta(tmp_path, {"imports": PARTIAL, "imports_partial": True})

        kept = _kept_stats(root, {"imports": FULL, "imports_partial": False})

        assert kept["imports"] == FULL

    def test_a_full_run_replaces_an_older_full_measurement(self, tmp_path):
        """A complete run is always authoritative over a complete predecessor."""
        root = _repo_with_meta(tmp_path, {"imports": PARTIAL, "imports_partial": False})

        kept = _kept_stats(root, {"imports": FULL, "imports_partial": False})

        assert kept["imports"] == FULL

    def test_an_empty_run_still_carries_the_previous_forward(self, tmp_path):
        root = _repo_with_meta(tmp_path, {"imports": FULL, "imports_partial": False})

        kept = _kept_stats(root, {"imports": {}, "imports_partial": True})

        assert kept["imports"] == FULL

    def test_a_partial_run_lands_when_nothing_was_measured_before(self, tmp_path):
        """Something beats nothing, as long as it is labelled."""
        root = _repo_with_meta(tmp_path, {})

        kept = _kept_stats(root, {"imports": PARTIAL, "imports_partial": True})

        assert kept["imports"] == PARTIAL
        assert kept["imports_partial"] is True


class TestStatusLine:
    def test_a_partial_measurement_says_so(self):
        line = _imports_coverage_line(PARTIAL, partial=True)

        assert "partial scan" in line
        assert "31/190" in line

    def test_a_full_measurement_stays_quiet(self):
        line = _imports_coverage_line(FULL, partial=False)

        assert "partial" not in line
        assert "46/217" in line
