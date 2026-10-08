# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Compatibility once secure mode is gone (0.15.0). A config
#              that still says mode = "secure" loads as assist with one
#              stderr notice per process, never raises, and the notice
#              shows in `cgh status` (human and --json) and `cgh doctor`.
#              `cgh init --secure` is still accepted and only prints a
#              deprecation note.

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import codegraph.core.config as config_mod
from codegraph.core.config import LEGACY_SECURE_MODE_NOTICE, load_config


@pytest.fixture
def fresh_notice(monkeypatch):
    """Each test sees a process that has not warned or suppressed yet."""
    monkeypatch.setattr(config_mod, "_legacy_mode_warned", False)
    monkeypatch.setattr(config_mod, "_legacy_mode_suppressed", False)


def _repo(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


class TestConfig:
    def test_secure_loads_as_assist_with_one_warning(
        self, tmp_path, capsys, fresh_notice
    ):
        root = _repo(tmp_path, '[codegraph]\nmode = "secure"\n')
        first = load_config(root)
        second = load_config(root)
        assert first.mode == second.mode == "assist"
        assert first.legacy_secure_mode is True
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count(LEGACY_SECURE_MODE_NOTICE) == 1
        assert captured.err.startswith("cgh: ")

    @pytest.mark.parametrize("body", ['[codegraph]\nmode = "assist"\n', ""])
    def test_assist_or_absent_is_silent(self, tmp_path, capsys, fresh_notice, body):
        cfg = load_config(_repo(tmp_path, body))
        assert cfg.mode == "assist" and cfg.legacy_secure_mode is False
        assert capsys.readouterr().err == ""

    def test_suppressed_notice_stays_quiet(self, tmp_path, capsys, fresh_notice):
        config_mod.suppress_legacy_mode_warning()
        load_config(_repo(tmp_path, '[codegraph]\nmode = "secure"\n'))
        assert capsys.readouterr().err == ""

    def test_odd_values_never_raise(self, tmp_path, fresh_notice):
        cfg = load_config(_repo(tmp_path, '[codegraph]\nmode = "  SECURE "\n'))
        assert cfg.mode == "assist" and cfg.legacy_secure_mode is True


class TestDeadKeys:
    BODY = '[plugin.summarize]\nbackend = "auto"\nallow_pii = true\negress = "strict"\n'

    def test_dead_keys_load_with_one_warning(self, tmp_path, capsys, fresh_notice):
        root = _repo(tmp_path, self.BODY)
        first = load_config(root)
        load_config(root)
        assert first.dead_keys == [
            "[plugin.summarize] allow_pii",
            "[plugin.summarize] egress",
        ]
        # Passed through untouched: the plugin table is not rewritten.
        assert first.plugin_tables["summarize"]["allow_pii"] is True
        err = capsys.readouterr().err
        assert err.count("cgh: ") == 1
        assert "[plugin.summarize] allow_pii, [plugin.summarize] egress" in err

    def test_secure_and_dead_keys_share_one_line(self, tmp_path, capsys, fresh_notice):
        root = _repo(tmp_path, '[codegraph]\nmode = "secure"\n\n' + self.BODY)
        load_config(root)
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert LEGACY_SECURE_MODE_NOTICE in err and "allow_pii" in err

    def test_suppressed_in_hooks(self, tmp_path, capsys, fresh_notice):
        config_mod.suppress_legacy_mode_warning()
        load_config(_repo(tmp_path, self.BODY))
        assert capsys.readouterr().err == ""

    def test_live_keys_are_silent(self, tmp_path, capsys, fresh_notice):
        cfg = load_config(_repo(tmp_path, '[plugin.summarize]\nbackend = "auto"\n'))
        assert cfg.dead_keys == []
        assert capsys.readouterr().err == ""

    def test_status_json_lists_them(self, tmp_path, capsys, fresh_notice):
        from codegraph.cli.commands_monitor import cmd_status

        root = _repo(tmp_path, self.BODY)
        cmd_status(Namespace(root=str(root), json=True, refresh=False, workers=False))
        captured = capsys.readouterr()
        notices = json.loads(captured.out)["notices"]
        assert len(notices) == 1 and "allow_pii" in notices[0]
        assert captured.err == ""


class TestStatusAndDoctor:
    def test_status_json_carries_the_notice(self, tmp_path, capsys, fresh_notice):
        from codegraph.cli.commands_monitor import cmd_status

        root = _repo(tmp_path, '[codegraph]\nmode = "secure"\n')
        cmd_status(Namespace(root=str(root), json=True, refresh=False, workers=False))
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["notices"] == [f"config.toml: {LEGACY_SECURE_MODE_NOTICE}"]
        assert LEGACY_SECURE_MODE_NOTICE not in captured.err  # shown once, inline

    def test_status_json_has_no_notice_when_clean(self, tmp_path, capsys):
        from codegraph.cli.commands_monitor import cmd_status

        root = _repo(tmp_path, "[codegraph]\n")
        cmd_status(Namespace(root=str(root), json=True, refresh=False, workers=False))
        assert json.loads(capsys.readouterr().out)["notices"] == []

    def test_status_human_output_shows_it(self, tmp_path, capsys, fresh_notice):
        from codegraph.cli.commands_monitor import cmd_status

        root = _repo(tmp_path, '[codegraph]\nmode = "secure"\n')
        cmd_status(Namespace(root=str(root), json=False, refresh=False, workers=False))
        out = " ".join(capsys.readouterr().out.split())
        assert "no longer supported" in out

    def test_doctor_shows_it_without_failing(self, tmp_path, capsys, fresh_notice):
        from codegraph.cli.commands_monitor import cmd_doctor

        root = _repo(tmp_path, '[codegraph]\nmode = "secure"\n')
        cmd_doctor(Namespace(root=str(root), strict=False))
        out = " ".join(capsys.readouterr().out.split())
        assert "!!" in out and "no longer supported" in out


class TestInitSecureFlag:
    def test_flag_prints_note_and_writes_nothing(self, tmp_path, capsys):
        from codegraph.cli.commands_init import _note_deprecated_secure_flag

        root = _repo(tmp_path, "[codegraph]\n")
        _note_deprecated_secure_flag(Namespace(secure=True))
        out = " ".join(capsys.readouterr().out.split())
        assert "--secure is deprecated" in out
        cfg = (root / ".codegraph" / "config.toml").read_text(encoding="utf-8")
        assert cfg == "[codegraph]\n"

    def test_no_flag_no_note(self, capsys):
        from codegraph.cli.commands_init import _note_deprecated_secure_flag

        _note_deprecated_secure_flag(Namespace(secure=False))
        assert capsys.readouterr().out == ""

    def test_parser_still_accepts_secure(self, monkeypatch):
        """Old scripts pass --secure: argparse must not reject it."""
        import codegraph.__main__ as cli

        seen = {}
        monkeypatch.setattr(cli, "cmd_init", lambda args: seen.update(vars(args)))
        monkeypatch.setattr(
            "sys.argv", ["cgh", "init", "--root", "/nonexistent", "--yes", "--secure"]
        )
        cli.main()
        assert seen["secure"] is True
