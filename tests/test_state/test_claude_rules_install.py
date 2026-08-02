# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Claude usage-guidelines install picks its mechanism
#              from the installed Claude Code version: rules dir when
#              supported (with migration of the legacy CLAUDE.md
#              block), legacy marker block otherwise, and legacy when
#              the version cannot be determined at all.

from __future__ import annotations

import codegraph.integrations.skill_installer as installer
from codegraph.integrations.skill_installer import (
    _USAGE_BLOCK_START,
    install_usage_guidelines,
)


class TestRulesSupported:
    def test_writes_rules_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: True)
        written = install_usage_guidelines(tmp_path, "claude")
        assert written and written.endswith(".claude/rules/cgh-usage.md")
        text = (tmp_path / ".claude" / "rules" / "cgh-usage.md").read_text()
        assert "codegraph" in text
        assert _USAGE_BLOCK_START not in text  # no markers needed, cgh owns it

    def test_migrates_the_legacy_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: False)
        install_usage_guidelines(tmp_path, "claude")  # legacy block lands
        claude_md = tmp_path / "CLAUDE.md"
        assert _USAGE_BLOCK_START in claude_md.read_text()

        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: True)
        install_usage_guidelines(tmp_path, "claude")
        assert _USAGE_BLOCK_START not in claude_md.read_text()
        assert (tmp_path / ".claude" / "rules" / "cgh-usage.md").exists()

    def test_user_content_survives_migration(self, tmp_path, monkeypatch):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My project\n\nMy own rules.\n", encoding="utf-8")
        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: False)
        install_usage_guidelines(tmp_path, "claude")
        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: True)
        install_usage_guidelines(tmp_path, "claude")
        text = claude_md.read_text()
        assert "My own rules." in text
        assert _USAGE_BLOCK_START not in text


class TestRulesUnsupported:
    def test_legacy_block_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(installer, "_claude_supports_rules", lambda: False)
        written = install_usage_guidelines(tmp_path, "claude")
        assert written and written.endswith("CLAUDE.md")
        assert _USAGE_BLOCK_START in (tmp_path / "CLAUDE.md").read_text()
        assert not (tmp_path / ".claude" / "rules").exists()


class TestVersionProbe:
    def _probe(self, monkeypatch, stdout=None, raises=None):
        import subprocess as sp

        def fake_run(*a, **k):
            if raises:
                raise raises
            from types import SimpleNamespace

            return SimpleNamespace(stdout=stdout)

        monkeypatch.setattr(sp, "run", fake_run)
        return installer._claude_supports_rules()

    def test_new_version_supports(self, monkeypatch):
        assert self._probe(monkeypatch, stdout="2.1.220 (Claude Code)") is True

    def test_old_version_does_not(self, monkeypatch):
        assert self._probe(monkeypatch, stdout="1.0.44 (Claude Code)") is False

    def test_missing_binary_falls_back(self, monkeypatch):
        assert self._probe(monkeypatch, raises=FileNotFoundError()) is False

    def test_garbage_output_falls_back(self, monkeypatch):
        assert self._probe(monkeypatch, stdout="not a version") is False
