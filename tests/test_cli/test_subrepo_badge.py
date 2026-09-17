# Copyright (c) 2026 ALTIKVA. All rights reserved.
# SPDX-License-Identifier: MIT AND CC-BY-NC-SA-4.0
#
# Project:     cgh (codegraph)
# Description: A child with no owner attached is the normal resting state:
#              the parent reads its database directly, so federated queries
#              work anyway. Calling that "down" reported a fault that was
#              not one.
# Author:      jndjama (Joy Ndjama)

from codegraph.cli.commands_monitor import _format_subrepos_cell


class TestSubrepoBadge:
    def test_a_child_without_an_owner_is_idle_not_down(self):
        cell = _format_subrepos_cell(
            [
                {
                    "name": "ondonne-api",
                    "ok": True,
                    "owner_alive": False,
                    "owner_port": None,
                }
            ]
        )
        assert "idle" in cell
        assert "down" not in cell

    def test_a_child_with_an_owner_shows_its_port(self):
        cell = _format_subrepos_cell(
            [
                {
                    "name": "ondonne-api",
                    "ok": True,
                    "owner_alive": True,
                    "owner_port": 54052,
                }
            ]
        )
        assert "up :54052" in cell

    def test_a_broken_child_still_shows_its_status(self):
        """Only `ok: False` is a real problem, and it keeps its own wording."""
        cell = _format_subrepos_cell(
            [
                {
                    "name": "gone",
                    "ok": False,
                    "status": "missing index",
                    "owner_alive": False,
                }
            ]
        )
        assert "missing index" in cell
        assert "idle" not in cell

    def test_no_subrepos_reads_as_none(self):
        assert "none" in _format_subrepos_cell([])
