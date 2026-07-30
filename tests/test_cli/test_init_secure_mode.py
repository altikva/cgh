# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The init wizard's secure-mode step: _set_config_mode edits
#              config.toml in place (template comment, existing value, or
#              missing section), cgh init --secure enables the mode
#              without prompting, and --yes alone never changes posture.

from __future__ import annotations

import tomllib
from argparse import Namespace
from pathlib import Path

from codegraph.cli.commands_init import _maybe_enable_secure_mode, _set_config_mode
from codegraph.core.config import generate_default_config
from codegraph.state.guard import guard_mode


def _init_cfg(tmp_path: Path, content: str | None = None) -> Path:
    (tmp_path / ".codegraph").mkdir()
    cfg = tmp_path / ".codegraph" / "config.toml"
    cfg.write_text(
        generate_default_config() if content is None else content, encoding="utf-8"
    )
    return cfg


class TestSetConfigMode:
    def test_replaces_the_template_comment(self, tmp_path):
        cfg = _init_cfg(tmp_path)
        assert _set_config_mode(tmp_path, "secure") is True
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["mode"] == "secure"
        assert guard_mode(tmp_path) == "secure"

    def test_replaces_an_existing_assignment(self, tmp_path):
        cfg = _init_cfg(tmp_path, '[codegraph]\nmode = "secure"\n')
        _set_config_mode(tmp_path, "assist")
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["mode"] == "assist"

    def test_inserts_when_section_has_no_mode(self, tmp_path):
        cfg = _init_cfg(tmp_path, "[codegraph]\nmax_file_size_kb = 500\n")
        _set_config_mode(tmp_path, "secure")
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["mode"] == "secure"
        assert data["codegraph"]["max_file_size_kb"] == 500

    def test_appends_section_when_absent(self, tmp_path):
        cfg = _init_cfg(tmp_path, "[mcp]\nauto_watch = true\n")
        _set_config_mode(tmp_path, "secure")
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["mode"] == "secure"
        assert data["mcp"]["auto_watch"] is True

    def test_no_config_returns_false(self, tmp_path):
        assert _set_config_mode(tmp_path, "secure") is False


class TestWizardStep:
    def test_secure_flag_enables_without_prompt(self, tmp_path):
        _init_cfg(tmp_path)
        args = Namespace(secure=True, yes=True)
        _maybe_enable_secure_mode(tmp_path, args, cg_style=None)
        assert guard_mode(tmp_path) == "secure"

    def test_yes_alone_keeps_assist(self, tmp_path):
        _init_cfg(tmp_path)
        args = Namespace(secure=False, yes=True)
        _maybe_enable_secure_mode(tmp_path, args, cg_style=None)
        assert guard_mode(tmp_path) == "assist"

    def test_already_secure_is_left_alone(self, tmp_path):
        cfg = _init_cfg(tmp_path, '[codegraph]\nmode = "secure"\n')
        before = cfg.read_text(encoding="utf-8")
        args = Namespace(secure=True, yes=True)
        _maybe_enable_secure_mode(tmp_path, args, cg_style=None)
        assert cfg.read_text(encoding="utf-8") == before
