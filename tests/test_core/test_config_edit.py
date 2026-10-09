# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: config.toml key edits made by add-dir, federate and clean. A
#              config without a [codegraph] table used to make `cgh add-dir`
#              print success and write nothing; edits must now create the table,
#              keep comments and other tables, and never claim an unwritten key.

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import pytest

from codegraph.analysis.federation import add_subrepo, remove_subrepo
from codegraph.cli.commands_graph import cmd_add_dir
from codegraph.core.config_edit import ConfigEditError, remove_key, set_key

NO_SECTION = """# my settings
[parsers.python]
enabled = true  # keep me

[plugin.codegen]
model = "x"
"""


def _cfg(root: Path, text: str) -> Path:
    path = root / ".codegraph" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _add_dir(root: Path, *paths: str) -> None:
    cmd_add_dir(argparse.Namespace(root=str(root), action="add", paths=list(paths)))


class TestSetKey:
    def test_creates_missing_table_and_keeps_the_rest(self, tmp_path):
        cfg = _cfg(tmp_path, NO_SECTION)
        set_key(cfg, "codegraph", "extra_dirs", ["../front"])
        text = cfg.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        assert data["codegraph"]["extra_dirs"] == ["../front"]
        assert data["parsers"]["python"]["enabled"] is True
        assert data["plugin"]["codegen"]["model"] == "x"
        assert "# my settings" in text and "# keep me" in text

    def test_replaces_multiline_array_in_its_own_table_only(self, tmp_path):
        cfg = _cfg(
            tmp_path,
            '[codegraph]\nextra_dirs = [\n  "a",\n  "b",\n]\nmax_file_size_kb = 9\n'
            '[other]\nextra_dirs = ["keep"]\n',
        )
        set_key(cfg, "codegraph", "extra_dirs", ["c"])
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"] == {"extra_dirs": ["c"], "max_file_size_kb": 9}
        assert data["other"]["extra_dirs"] == ["keep"]

    def test_commented_key_is_not_mistaken_for_a_real_one(self, tmp_path):
        cfg = _cfg(tmp_path, '[codegraph]\n# extra_dirs = ["../my-frontend"]\n')
        set_key(cfg, "codegraph", "extra_dirs", ["../front"])
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["extra_dirs"] == ["../front"]

    def test_invalid_toml_is_left_untouched(self, tmp_path):
        cfg = _cfg(tmp_path, "[codegraph\nbroken = \n")
        before = cfg.read_text(encoding="utf-8")
        with pytest.raises(ConfigEditError):
            set_key(cfg, "codegraph", "extra_dirs", ["x"])
        assert cfg.read_text(encoding="utf-8") == before

    def test_remove_key(self, tmp_path):
        cfg = _cfg(tmp_path, '[codegraph]\nextra_dirs = ["a"]\nmax_file_size_kb = 9\n')
        assert remove_key(cfg, "codegraph", "extra_dirs") is True
        assert tomllib.loads(cfg.read_text(encoding="utf-8")) == {
            "codegraph": {"max_file_size_kb": 9}
        }
        assert remove_key(cfg, "codegraph", "extra_dirs") is False


class TestAddDirWithoutSection:
    def test_add_dir_writes_the_entry(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()
        (tmp_path / "front").mkdir()
        cfg = _cfg(root, NO_SECTION)
        monkeypatch.chdir(root)
        _add_dir(root, "../front")
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["extra_dirs"] == ["../front"]
        assert data["plugin"]["codegen"]["model"] == "x"

    def test_add_dir_over_default_template_comment(self, tmp_path, monkeypatch):
        from codegraph.core.config import generate_default_config

        root = tmp_path / "repo"
        root.mkdir()
        (tmp_path / "front").mkdir()
        cfg = _cfg(root, generate_default_config())
        monkeypatch.chdir(root)
        _add_dir(root, "../front")
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["extra_dirs"] == ["../front"]

    def test_add_dir_fails_loudly_on_unreadable_config(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()
        (tmp_path / "front").mkdir()
        _cfg(root, "[codegraph]\nextra_dirs = []\n")
        cfg = root / ".codegraph" / "config.toml"
        monkeypatch.chdir(root)

        def _boom(*_a, **_k):
            raise ConfigEditError("nope")

        monkeypatch.setattr("codegraph.core.config_edit.set_key", _boom)
        with pytest.raises(SystemExit):
            _add_dir(root, "../front")
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["codegraph"] == {
            "extra_dirs": []
        }


class TestFederateKeepsTheFile:
    def test_add_and_remove_preserve_comments_and_nested_tables(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        cfg = _cfg(parent, NO_SECTION)
        add_subrepo(parent, child)
        text = cfg.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        assert data["codegraph"]["subrepos"] == ["./child"]
        assert data["parsers"]["python"]["enabled"] is True
        assert "# keep me" in text
        assert remove_subrepo(parent, child) is True
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["codegraph"]["subrepos"] == []
        assert data["plugin"]["codegen"]["model"] == "x"
