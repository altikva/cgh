# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: AgentIntegration surface tests: the registry serves the
#              built-ins plus plugin-registered integrations, the
#              deprecated guard methods stay on the protocol but the
#              built-ins install nothing, and the Bob adapter detects
#              its tool and copies the skills verbatim.

from __future__ import annotations

import json

import pytest

import codegraph.plugins as plugins
from codegraph.integrations.base import (
    AgentIntegration,
    all_integrations,
    get_integration,
)
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    plugins._reset_for_tests()
    yield
    store.reset_for_tests()
    plugins._reset_for_tests()


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

    def test_builtins_no_longer_install_a_guard(self, tmp_path):
        for integration in all_integrations()[:5]:
            assert integration.guard_spec().level == "none"
            assert integration.install_guard(tmp_path) is False
            assert integration.guard_installed(tmp_path) is False
        assert not any(tmp_path.iterdir())  # nothing written anywhere

    def test_claude_hook_specs_drop_the_guard(self):
        from codegraph.cli.commands_init import _claude_hook_specs

        commands = json.dumps(_claude_hook_specs("cgh"))
        assert "_hook_guard" not in commands and "cgh-guard" not in commands


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
