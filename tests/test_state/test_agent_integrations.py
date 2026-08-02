# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: AgentIntegration surface tests: the registry serves the
#              four built-ins plus plugin-registered integrations, guard
#              hook installation is idempotent per agent (Gemini
#              BeforeTool settings, Codex hooks.json + feature flag,
#              Claude via the shared spec machinery), the Gemini tool
#              names flow through the guard, and the Codex handler
#              speaks its stdout JSON protocol.

from __future__ import annotations

import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

import codegraph.plugins as plugins
from codegraph.integrations.base import (
    AgentIntegration,
    all_integrations,
    get_integration,
)
from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store
from codegraph.state.guard import check_tool_call


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    plugins._reset_for_tests()
    yield
    store.reset_for_tests()
    plugins._reset_for_tests()


def _repo(tmp_path: Path, mode: str = "assist") -> Path:
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


class TestRegistry:
    def test_five_builtins_present(self):
        names = [i.name for i in all_integrations()]
        assert names[:5] == ["claude", "cursor", "codex", "gemini", "bob"]

    def test_plugin_integration_joins_the_registry(self):
        class AcmeIntegration:
            name = "acme"
            display = "Acme CLI"

            def detect(self, root):
                return True

            def install_instructions(self, root):
                return []

            def guard_spec(self):
                from codegraph.integrations.base import GuardSpec

                return GuardSpec(level="advisory")

            def install_guard(self, root):
                return False

            def guard_installed(self, root):
                return False

        plugins._registries.extensions.setdefault("integration", []).append(
            ("acme-plugin", AcmeIntegration())
        )
        registry = {i.name for i in all_integrations()}
        assert "acme" in registry
        acme = get_integration("acme")
        assert isinstance(acme, AgentIntegration)
        assert acme.display == "Acme CLI"

    def test_every_builtin_declares_a_guard_spec(self):
        levels = {i.name: i.guard_spec().level for i in all_integrations()}
        assert levels["claude"] == "enforce"
        assert levels["gemini"] == "enforce"
        assert levels["codex"] == "partial"
        assert levels["cursor"] == "none"
        assert levels["bob"] == "partial"


class TestGeminiAdapter:
    def test_install_guard_writes_beforetool_and_is_idempotent(self, tmp_path):
        gemini = get_integration("gemini")
        assert gemini.install_guard(tmp_path) is True
        assert gemini.install_guard(tmp_path) is False  # idempotent
        assert gemini.guard_installed(tmp_path)

        settings = json.loads(
            (tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8")
        )
        bucket = settings["hooks"]["BeforeTool"]
        assert len(bucket) == 1
        assert "read_file" in bucket[0]["matcher"]
        assert "run_shell_command" in bucket[0]["matcher"]
        assert "_hook_guard" in bucket[0]["hooks"][0]["command"]

    def test_install_preserves_existing_settings(self, tmp_path):
        path = tmp_path / ".gemini" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        get_integration("gemini").install_guard(tmp_path)
        settings = json.loads(path.read_text(encoding="utf-8"))
        assert settings["theme"] == "dark"
        assert "BeforeTool" in settings["hooks"]

    def test_gemini_tool_names_flow_through_the_guard(self, tmp_path):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")

        assert check_tool_call(root, "read_file", {"absolute_path": barred}, "assist")
        assert check_tool_call(
            root, "read_many_files", {"paths": [str(root / "ok.py"), barred]}, "assist"
        )
        assert check_tool_call(
            root, "run_shell_command", {"command": f"cat {barred}"}, "assist"
        )
        assert (
            check_tool_call(
                root, "read_file", {"absolute_path": str(root / "ok.py")}, "assist"
            )
            is None
        )


class TestCodexAdapter:
    def test_install_guard_writes_hooks_and_feature_flag(self, tmp_path):
        codex = get_integration("codex")
        assert codex.install_guard(tmp_path) is True
        assert codex.install_guard(tmp_path) is False  # idempotent
        assert codex.guard_installed(tmp_path)

        hooks = json.loads(
            (tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8")
        )
        assert any(
            "_hook_guard_codex" in entry["command"]
            for entry in hooks["hooks"]["PreToolUse"]
        )
        config = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
        assert "codex_hooks = true" in config

    def test_feature_flag_appends_to_existing_config(self, tmp_path):
        path = tmp_path / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('model = "o4"\n', encoding="utf-8")
        get_integration("codex").install_guard(tmp_path)
        text = path.read_text(encoding="utf-8")
        assert 'model = "o4"' in text and "codex_hooks = true" in text

    def _run_codex_hook(self, monkeypatch, payload: dict) -> dict | None:
        from codegraph.cli.commands_guard import cmd_hook_guard_codex

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        cmd_hook_guard_codex(Namespace())
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def test_codex_handler_blocks_shell_on_barred_path(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        barred = _bar(root, "payroll.xlsx")
        decision = self._run_codex_hook(
            monkeypatch,
            {
                "cwd": str(root),
                "tool_name": "shell",
                "tool_input": {"command": ["cat", barred]},
            },
        )
        assert decision is not None
        # Both accepted spellings of the deny, vendor docs disagree.
        assert decision["decision"] == "block"
        assert decision["permissionDecision"] == "deny"
        assert "flagged confidential" in decision["reason"]

    def test_codex_handler_stays_silent_on_clean_command(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        _bar(root, "payroll.xlsx")
        decision = self._run_codex_hook(
            monkeypatch,
            {
                "cwd": str(root),
                "tool_name": "shell",
                "tool_input": {"command": ["git", "status"]},
            },
        )
        assert decision is None


class TestClaudeAdapter:
    def test_install_guard_through_shared_specs(self, tmp_path):
        claude = get_integration("claude")
        assert claude.install_guard(tmp_path) is True
        assert claude.guard_installed(tmp_path)
        # Idempotent: a second run adds nothing.
        assert claude.install_guard(tmp_path) is False


class TestBobAdapter:
    def _bob(self):
        from codegraph.integrations.base import BobIntegration

        return BobIntegration()

    def test_detects_bob_dir_and_bobignore(self, tmp_path, monkeypatch):
        import shutil as _shutil

        monkeypatch.setattr(_shutil, "which", lambda t: None)
        bob = self._bob()
        assert bob.detect(tmp_path) is False
        (tmp_path / ".bobignore").write_text("dist/\n", encoding="utf-8")
        assert bob.detect(tmp_path) is True
        (tmp_path / ".bobignore").unlink()
        (tmp_path / ".bob").mkdir()
        assert bob.detect(tmp_path) is True

    def test_install_copies_skills_verbatim(self, tmp_path, monkeypatch):
        import codegraph.integrations.skill_installer as installer

        src = tmp_path / "bundled" / "cgh-usage"
        src.mkdir(parents=True)
        skill_md = "---\nname: cgh-usage\ndescription: use the graph\n---\nBody.\n"
        (src / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (src / "extra.md").write_text("supporting file\n", encoding="utf-8")
        monkeypatch.setattr(
            installer,
            "_iter_skills",
            lambda: [("cgh-usage", {"name": "cgh-usage"}, "Body.", src)],
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        names = self._bob().install_instructions(repo)
        assert names == ["cgh-usage"]
        dest = repo / ".bob" / "skills" / "cgh-usage"
        assert (dest / "SKILL.md").read_text(encoding="utf-8") == skill_md
        assert (dest / "extra.md").exists()

    def test_bobignore_sync_secure_mode(self, tmp_path):
        from codegraph.state.guard import sync_bobignore

        root = _repo(tmp_path, mode="secure")
        (root / "secrets.env").write_text("x", encoding="utf-8")
        _bar(root, "secrets.env")

        added, removed = sync_bobignore(root)
        # The barred file plus the standing .codegraph/ index entry.
        assert (added, removed) == (2, 0)
        content = (root / ".bobignore").read_text(encoding="utf-8")
        assert "secrets.env" in content
        assert ".codegraph/" in content
        assert "cgh guard" in content

        # Idempotent: second sync changes nothing.
        assert sync_bobignore(root) == (0, 0)
        assert self._bob().guard_installed(root) is True

    def test_bobignore_preserves_user_lines(self, tmp_path):
        from codegraph.state.guard import sync_bobignore

        root = _repo(tmp_path, mode="secure")
        (root / ".bobignore").write_text("node_modules/\n", encoding="utf-8")
        (root / "key.pem").write_text("x", encoding="utf-8")
        _bar(root, "key.pem")

        sync_bobignore(root)
        content = (root / ".bobignore").read_text(encoding="utf-8")
        assert content.startswith("node_modules/\n")
        assert "key.pem" in content

    def test_bobignore_noop_in_assist_mode(self, tmp_path):
        from codegraph.state.guard import sync_bobignore

        root = _repo(tmp_path, mode="assist")
        (root / "key.pem").write_text("x", encoding="utf-8")
        _bar(root, "key.pem")

        assert sync_bobignore(root) == (0, 0)
        assert not (root / ".bobignore").exists()

    def test_bobignore_clears_when_finding_gone(self, tmp_path):
        from codegraph.state.guard import sync_bobignore

        root = _repo(tmp_path, mode="secure")
        (root / "key.pem").write_text("x", encoding="utf-8")
        barred = _bar(root, "key.pem")
        sync_bobignore(root)

        store.purge_file_findings(root, barred)
        added, removed = sync_bobignore(root)
        assert (added, removed) == (0, 1)
        content = (root / ".bobignore").read_text(encoding="utf-8")
        assert "key.pem" not in content
