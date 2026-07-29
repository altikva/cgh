# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Guard tests: blocking facts from the finding store, tool
#              call checks per tool, Bash matching in both postures, the
#              hook handler's exit codes and fail posture, and the static
#              deny-rule sync that never touches user-authored rules.

from __future__ import annotations

import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store
from codegraph.state.guard import (
    blocking_paths,
    check_bash,
    check_tool_call,
    sync_static_rules,
)


@pytest.fixture(autouse=True)
def clean_store():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _repo(tmp_path: Path, mode: str = "assist") -> Path:
    (tmp_path / ".codegraph").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".codegraph" / "config.toml").write_text(
        f'[codegraph]\nmode = "{mode}"\n', encoding="utf-8"
    )
    return tmp_path


def _bar(root: Path, name: str, key: str = "confidential", value: str = "true",
         severity: str = "block") -> str:  # fmt: skip
    path = str((root / name).resolve())
    store.record_findings(
        root, path, "test", [ScanFinding(key=key, value=value, severity=severity)]
    )
    return path


class TestBlockingFacts:
    def test_confidential_and_block_severity_bar(self, tmp_path):
        root = _repo(tmp_path)
        a = _bar(root, "a.py")
        b = _bar(root, "b.py", key="secret.aws_key", value="1")
        _bar(root, "c.py", key="pii.email", value="2", severity="warn")
        assert blocking_paths(root) == {a, b}

    def test_human_public_label_does_not_bar(self, tmp_path):
        root = _repo(tmp_path)
        _bar(root, "a.py", value="false", severity="info")
        assert blocking_paths(root) == set()


class TestToolCalls:
    def test_read_of_barred_file_denied(self, tmp_path):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        assert check_tool_call(root, "Read", {"file_path": barred}, "assist")
        assert (
            check_tool_call(root, "Read", {"file_path": str(root / "ok.py")}, "assist")
            is None
        )

    def test_relative_path_is_resolved(self, tmp_path):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        assert check_tool_call(root, "Read", {"file_path": "payroll.xlsx"}, "assist")

    def test_grep_on_directory_passes(self, tmp_path):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        assert check_tool_call(root, "Grep", {"path": str(root)}, "assist") is None


class TestBashMatching:
    def test_assist_blocks_read_commands_only(self, tmp_path):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        assert check_bash(root, f"cat {barred}", "assist")
        assert check_bash(root, f"head -5 {barred} | wc -l", "assist")
        # Not a read command: assist lets it through.
        assert check_bash(root, f"ls -la {barred}", "assist") is None

    def test_secure_blocks_any_verb(self, tmp_path):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        assert check_bash(root, f"ls -la {barred}", "secure")
        assert check_bash(root, f"cp {barred} /tmp/x", "secure")

    def test_clean_command_passes_everywhere(self, tmp_path):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        assert check_bash(root, "git status", "secure") is None
        assert check_bash(root, "cat README.md", "assist") is None


class TestHookHandler:
    def _run(self, monkeypatch, payload: dict) -> tuple[int, str]:
        from codegraph.cli.commands_guard import cmd_hook_guard

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        err = io.StringIO()
        monkeypatch.setattr("sys.stderr", err)
        try:
            cmd_hook_guard(Namespace())
        except SystemExit as exc:
            return int(exc.code or 0), err.getvalue()
        return 0, err.getvalue()

    def test_denies_with_exit_2_and_reason(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        code, err = self._run(
            monkeypatch,
            {
                "cwd": str(root),
                "tool_name": "Read",
                "tool_input": {"file_path": barred},
            },
        )
        assert code == 2
        assert "flagged confidential" in err

    def test_allows_clean_read(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        code, _ = self._run(
            monkeypatch,
            {
                "cwd": str(root),
                "tool_name": "Read",
                "tool_input": {"file_path": str(root / "ok.py")},
            },
        )
        assert code == 0

    def test_non_cgh_repo_is_a_noop(self, tmp_path, monkeypatch):
        code, err = self._run(
            monkeypatch,
            {
                "cwd": str(tmp_path),
                "tool_name": "Read",
                "tool_input": {"file_path": "x"},
            },
        )
        assert code == 0 and err == ""

    def test_fail_posture(self, tmp_path, monkeypatch):
        root_a = _repo(tmp_path / "assist_repo")
        root_s = _repo(tmp_path / "secure_repo", mode="secure")

        def boom(*a, **k):
            raise RuntimeError("store corrupted")

        import codegraph.state.guard as guard

        monkeypatch.setattr(guard, "check_tool_call", boom)

        payload = {"tool_name": "Read", "tool_input": {"file_path": "x"}}
        code, _ = self._run(monkeypatch, {**payload, "cwd": str(root_a)})
        assert code == 0  # assist fails open

        code, err = self._run(monkeypatch, {**payload, "cwd": str(root_s)})
        assert code == 2  # secure fails closed
        assert "failed closed" in err


class TestStaticRules:
    def test_sync_adds_and_removes_only_ours(self, tmp_path):
        root = _repo(tmp_path, mode="secure")
        barred = _bar(root, "payroll.xlsx")
        settings = root / ".claude" / "settings.local.json"
        settings.parent.mkdir()
        settings.write_text(
            json.dumps({"permissions": {"deny": ["Read(/user/own.txt)"]}}),
            encoding="utf-8",
        )

        added, removed = sync_static_rules(root)
        assert (added, removed) == (1, 0)
        deny = json.loads(settings.read_text())["permissions"]["deny"]
        assert f"Read({barred})" in deny
        assert "Read(/user/own.txt)" in deny

        # The file is cleared: our rule goes, the user's stays.
        store.record_findings(root, barred, "test", [])
        added, removed = sync_static_rules(root)
        assert (added, removed) == (0, 1)
        deny = json.loads(settings.read_text())["permissions"]["deny"]
        assert deny == ["Read(/user/own.txt)"]

    def test_sync_is_a_noop_in_assist(self, tmp_path):
        root = _repo(tmp_path, mode="assist")
        _bar(root, "payroll.xlsx")
        assert sync_static_rules(root) == (0, 0)
        assert not (root / ".claude" / "settings.local.json").exists()
