# Copyright (c) 2026 ALTIKVA. All rights reserved.
# SPDX-License-Identifier: MIT AND CC-BY-NC-SA-4.0
#
# Project:     cgh (codegraph)
# Description: The memory and plan scans belong to a plain `cgh index`:
#              they live outside the repo, so nothing in the file walk
#              covers them and a reset used to drop them silently.
# Author:      jndjama (Joy Ndjama)

from codegraph.cli.commands_index import _scan_claude_state


class TestScanClaudeState:
    def test_runs_both_scans(self, monkeypatch, capsys):
        calls: list[str] = []

        def fake_memory(root, verbose=False):
            calls.append(f"memory:{root}")
            return {"indexed": 18, "skipped": 0, "removed": 0}

        def fake_plans(root, verbose=False):
            calls.append(f"plans:{root}")
            return {"indexed": 2, "skipped": 0, "removed": 0}

        monkeypatch.setattr(
            "codegraph.claude_state.memory.scan_memory_dir", fake_memory
        )
        monkeypatch.setattr("codegraph.claude_state.plans.scan_plan_dir", fake_plans)

        _scan_claude_state("/tmp/repo")

        assert calls == ["memory:/tmp/repo", "plans:/tmp/repo"]
        out = capsys.readouterr().out
        assert "18" in out and "2" in out

    def test_an_unreadable_directory_does_not_fail_the_index(self, monkeypatch, capsys):
        """Indexing the repo must survive a ~/.claude that cannot be read."""

        def boom(root, verbose=False):
            raise OSError("permission denied")

        def fake_plans(root, verbose=False):
            return {"indexed": 0, "skipped": 0, "removed": 0}

        monkeypatch.setattr("codegraph.claude_state.memory.scan_memory_dir", boom)
        monkeypatch.setattr("codegraph.claude_state.plans.scan_plan_dir", fake_plans)

        _scan_claude_state("/tmp/repo")

        assert "unavailable" in capsys.readouterr().out


class TestIndexFlag:
    def test_index_accepts_no_claude_state(self):
        from codegraph.__main__ import _LogoArgumentParser, _register_setup_and_serve

        ap = _LogoArgumentParser(prog="codegraph", add_help=False)
        sub = ap.add_subparsers(dest="cmd", parser_class=_LogoArgumentParser)
        _register_setup_and_serve(sub)

        args = ap.parse_args(["index", "--no-claude-state"])

        assert args.cmd == "index"
        assert args.no_claude_state is True

    def test_index_runs_the_scans_by_default(self):
        """The whole point: a plain `cgh index` must not skip them."""
        from codegraph.__main__ import _LogoArgumentParser, _register_setup_and_serve

        ap = _LogoArgumentParser(prog="codegraph", add_help=False)
        sub = ap.add_subparsers(dest="cmd", parser_class=_LogoArgumentParser)
        _register_setup_and_serve(sub)

        args = ap.parse_args(["index"])

        assert args.no_claude_state is False
