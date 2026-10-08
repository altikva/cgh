# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: What is left of the guard since 0.15.0. The hook entry
#              points always allow and print nothing, even on a file an
#              older version would have blocked and even with a legacy
#              mode = "secure" config. The cleanup removes exactly what
#              cgh wrote (sidecar-recorded Claude deny rules, the managed
#              .bobignore block, the guard hook entries) and leaves every
#              user entry in place.

from __future__ import annotations

import io
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store
from codegraph.state.guard import cleanup_guard_leftovers, sync_static_rules

_BLOCK = (
    "# >>> cgh guard (managed, do not edit) >>>\n"
    ".codegraph/\nkey.pem\n"
    "# <<< cgh guard <<<\n"
)


@pytest.fixture(autouse=True)
def clean_store():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _repo(tmp_path: Path, mode: str = "secure") -> Path:
    (tmp_path / ".codegraph").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".codegraph" / "config.toml").write_text(
        f'[codegraph]\nmode = "{mode}"\n', encoding="utf-8"
    )
    return tmp_path


def _bar(root: Path, name: str) -> str:
    path = str((root / name).resolve())
    store.record_findings(
        root,
        path,
        "test",
        [ScanFinding(key="confidential", value="true", severity="block")],
    )
    return path


def _hook_entry(matcher: str, command: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}


class TestHookAlwaysAllows:
    def _run(self, monkeypatch, handler, payload: dict) -> tuple[int, str, str]:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", err)
        try:
            handler(Namespace())
        except SystemExit as exc:
            return int(exc.code or 0), out.getvalue(), err.getvalue()
        return 0, out.getvalue(), err.getvalue()

    @pytest.mark.parametrize(
        "tool, tool_input",
        [
            ("Read", {"file_path": "{barred}"}),
            ("Bash", {"command": "cat {barred}"}),
            ("Read", {"file_path": "{root}/.codegraph/findings.db"}),
            ("Bash", {"command": "sqlite3 .codegraph/findings.db .dump"}),
        ],
    )
    def test_claude_hook_allows_what_used_to_be_denied(
        self, tmp_path, monkeypatch, tool, tool_input
    ):
        from codegraph.cli.commands_guard import cmd_hook_guard

        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        filled = {k: v.format(barred=barred, root=root) for k, v in tool_input.items()}
        code, out, err = self._run(
            monkeypatch,
            cmd_hook_guard,
            {"cwd": str(root), "tool_name": tool, "tool_input": filled},
        )
        assert (code, out, err) == (0, "", "")

    def test_codex_hook_prints_no_decision(self, tmp_path, monkeypatch):
        from codegraph.cli.commands_guard import cmd_hook_guard_codex

        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        code, out, err = self._run(
            monkeypatch,
            cmd_hook_guard_codex,
            {
                "cwd": str(root),
                "tool_name": "shell",
                "tool_input": {"command": ["cat", barred]},
            },
        )
        assert (code, out, err) == (0, "", "")

    def test_garbage_payload_still_allows(self, monkeypatch):
        from codegraph.cli.commands_guard import cmd_hook_guard

        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        cmd_hook_guard(Namespace())  # no raise, no exit

    @pytest.mark.parametrize("command", ["_hook_guard", "_hook_guard_codex"])
    def test_real_command_is_silent_with_legacy_secure_config(self, tmp_path, command):
        """End to end through the CLI: exit 0, empty stdout AND stderr,
        even with the trailing marker an agent config passes along."""
        root = _repo(tmp_path)
        payload = json.dumps(
            {"cwd": str(root), "tool_name": "Read", "tool_input": {"file_path": "x"}}
        )
        proc = subprocess.run(
            [sys.executable, "-m", "codegraph", command, "#", "cgh-guard"],
            input=payload,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=60,
        )
        assert proc.returncode == 0
        assert proc.stdout == ""
        assert proc.stderr == ""


class TestCleanupClaudeRules:
    def _settings(self, root: Path, deny: list[str], **extra) -> Path:
        path = root / ".claude" / "settings.local.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"permissions": {"deny": deny}, **extra}), encoding="utf-8"
        )
        return path

    def _sidecar(self, root: Path, rules: list[str]) -> Path:
        path = root / ".codegraph" / "guard_denies.json"
        path.write_text(json.dumps(rules), encoding="utf-8")
        return path

    def test_removes_only_sidecar_rules(self, tmp_path):
        root = _repo(tmp_path)
        ours = ["Read(.codegraph/**)", f"Read({root}/payroll.xlsx)"]
        user = ["Read(./.env)", "Bash(rm -rf *)"]
        settings = self._settings(root, user[:1] + ours + user[1:], model="opus")
        sidecar = self._sidecar(root, ours)

        report = cleanup_guard_leftovers(root)

        assert sorted(report.claude_rules) == sorted(ours)
        assert report.sidecar_removed and not sidecar.exists()
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["permissions"]["deny"] == user
        assert data["model"] == "opus"

    def test_rule_identical_to_ours_is_kept_without_sidecar(self, tmp_path):
        """Without the sidecar cgh cannot prove it wrote a rule: hands off."""
        root = _repo(tmp_path)
        settings = self._settings(root, ["Read(.codegraph/**)"])
        report = cleanup_guard_leftovers(root)
        assert report.claude_rules == []
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["permissions"]["deny"] == ["Read(.codegraph/**)"]

    def test_empty_deny_list_is_dropped(self, tmp_path):
        root = _repo(tmp_path)
        settings = self._settings(root, ["Read(.codegraph/**)"])
        self._sidecar(root, ["Read(.codegraph/**)"])
        cleanup_guard_leftovers(root)
        assert "permissions" not in json.loads(settings.read_text(encoding="utf-8"))

    def test_unparseable_settings_are_left_alone(self, tmp_path):
        root = _repo(tmp_path)
        path = root / ".claude" / "settings.local.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")
        sidecar = self._sidecar(root, ["Read(.codegraph/**)"])
        report = cleanup_guard_leftovers(root)
        assert path.read_text(encoding="utf-8") == "{ not json"
        assert sidecar.exists()  # kept so a later run can finish
        assert not report.sidecar_removed

    def test_plugin_api_shim_cleans_and_never_adds(self, tmp_path):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        self._settings(root, ["Read(.codegraph/**)", "Read(./.env)"])
        self._sidecar(root, ["Read(.codegraph/**)"])
        assert sync_static_rules(root) == (0, 1)
        assert sync_static_rules(root) == (0, 0)


class TestCleanupBobignore:
    def test_removes_block_keeps_user_lines(self, tmp_path):
        root = _repo(tmp_path)
        path = root / ".bobignore"
        path.write_text("node_modules/\n" + _BLOCK + "dist/\n", encoding="utf-8")
        report = cleanup_guard_leftovers(root)
        assert report.bobignore_lines == [".codegraph/", "key.pem"]
        assert path.read_text(encoding="utf-8") == "node_modules/\ndist/\n"

    def test_file_holding_only_the_block_is_deleted(self, tmp_path):
        root = _repo(tmp_path)
        (root / ".bobignore").write_text(_BLOCK, encoding="utf-8")
        cleanup_guard_leftovers(root)
        assert not (root / ".bobignore").exists()

    def test_user_file_without_block_is_untouched(self, tmp_path):
        root = _repo(tmp_path)
        path = root / ".bobignore"
        path.write_text(".codegraph/\nsecrets/\n", encoding="utf-8")
        report = cleanup_guard_leftovers(root)
        assert not report.changed
        assert path.read_text(encoding="utf-8") == ".codegraph/\nsecrets/\n"


class TestCleanupHooks:
    def test_claude_guard_hook_removed_others_kept(self, tmp_path):
        root = _repo(tmp_path)
        path = root / ".claude" / "settings.local.json"
        path.parent.mkdir(parents=True)
        hooks = {
            "PreToolUse": [
                _hook_entry("Read", "cgh _hook_precheck_read  # cgh-precheck-read"),
                _hook_entry("Read|Grep|Glob|Bash", "cgh _hook_guard  # cgh-guard"),
                _hook_entry("Bash", "my-own-guard.sh"),
            ]
        }
        path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

        report = cleanup_guard_leftovers(root)

        assert report.hooks == [".claude/settings.local.json"]
        bucket = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        commands = [h["hooks"][0]["command"] for h in bucket]
        assert commands == [
            "cgh _hook_precheck_read  # cgh-precheck-read",
            "my-own-guard.sh",
        ]

    def test_gemini_and_codex_guard_hooks_removed(self, tmp_path):
        root = _repo(tmp_path)
        gemini = root / ".gemini" / "settings.json"
        gemini.parent.mkdir()
        gemini.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "BeforeTool": [
                            _hook_entry("read_file", "cgh _hook_guard  # cgh-guard")
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        codex = root / ".codex" / "hooks.json"
        codex.parent.mkdir()
        codex.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"command": "cgh _hook_guard_codex"},
                            {"command": "mine.sh"},
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        flag = root / ".codex" / "config.toml"
        flag.write_text("[features]\ncodex_hooks = true\n", encoding="utf-8")

        report = cleanup_guard_leftovers(root)

        assert set(report.hooks) == {".gemini/settings.json", ".codex/hooks.json"}
        g = json.loads(gemini.read_text(encoding="utf-8"))
        assert g == {"theme": "dark", "hooks": {}}
        c = json.loads(codex.read_text(encoding="utf-8"))
        assert c["hooks"]["PreToolUse"] == [{"command": "mine.sh"}]
        assert "codex_hooks = true" in flag.read_text(encoding="utf-8")

    def test_idempotent(self, tmp_path):
        root = _repo(tmp_path)
        assert not cleanup_guard_leftovers(root).changed
        assert not cleanup_guard_leftovers(root).changed


class TestGuardCommand:
    def test_prints_deprecation_and_cleans(self, tmp_path, capsys):
        from codegraph.cli.commands_guard import cmd_guard

        root = _repo(tmp_path)
        (root / ".bobignore").write_text(_BLOCK, encoding="utf-8")
        cmd_guard(Namespace(root=str(root), action="sync"))
        out = capsys.readouterr().out
        assert "deprecated" in out
        assert ".bobignore" in out
        assert not (root / ".bobignore").exists()

    def test_setup_runs_the_cleanup(self, tmp_path, monkeypatch):
        from codegraph.cli.commands_init import cmd_setup

        root = _repo(tmp_path)
        (root / ".bobignore").write_text("dist/\n" + _BLOCK, encoding="utf-8")
        monkeypatch.chdir(root)
        cmd_setup(Namespace(root=str(root), target="cursor"))
        assert (root / ".bobignore").read_text(encoding="utf-8") == "dist/\n"


def test_cleanup_keeps_a_user_hook_sharing_the_guard_group(tmp_path: Path) -> None:
    """Only cgh's guard command leaves a hook group; a user hook that sits
    in the same group (same matcher) stays, and so does the group."""
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    user_hook = {"type": "command", "command": "my-audit.sh"}
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read|Grep|Glob|Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "cgh _hook_guard  # cgh-guard",
                                },
                                user_hook,
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "cgh _hook_guard  # cgh-guard",
                                }
                            ],
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    report = cleanup_guard_leftovers(tmp_path)
    data = json.loads(settings.read_text(encoding="utf-8"))
    groups = data["hooks"]["PreToolUse"]
    assert groups == [{"matcher": "Read|Grep|Glob|Bash", "hooks": [user_hook]}]
    assert ".claude/settings.local.json" in report.hooks
