# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Federation config.toml must stay valid on Windows. Subrepo paths
#              are stored with forward slashes, and the writer escapes
#              backslashes so a path never produces an invalid TOML escape (the
#              bug where 18 Windows subrepos silently read back as none).

from __future__ import annotations

import tomllib

from codegraph.analysis.federation import (
    _read_config_toml,
    _toml_escape,
    _write_config_toml,
    add_subrepo,
    resolve_children,
)


class TestTomlEscape:
    def test_escapes_backslash(self):
        assert _toml_escape("a\\b") == "a\\\\b"

    def test_escapes_quote(self):
        assert _toml_escape('a"b') == 'a\\"b'

    def test_backslash_before_quote(self):
        assert _toml_escape('a\\"b') == 'a\\\\\\"b'


class TestWriterRoundTrip:
    def test_backslash_path_stays_valid_toml(self, tmp_path):
        # A Windows-style path with backslashes (incl. \s, which is an invalid
        # raw TOML escape) must round-trip through the writer and parse.
        cfg = tmp_path / "config.toml"
        data = {"codegraph": {"subrepos": ["./edf-sa\\services-backup"]}}
        _write_config_toml(cfg, data)
        parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert parsed["codegraph"]["subrepos"] == ["./edf-sa\\services-backup"]
        # And our own resilient reader agrees.
        assert _read_config_toml(cfg)["codegraph"]["subrepos"] == ["./edf-sa\\services-backup"]


class TestAddSubrepoForwardSlash:
    def test_nested_subrepo_stored_with_forward_slashes(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "edf-sa" / "services-backup"
        child.mkdir(parents=True)

        add_subrepo(parent, child)

        raw = (parent / ".codegraph" / "config.toml").read_text(encoding="utf-8")
        assert "./edf-sa/services-backup" in raw
        assert "\\" not in raw  # no backslash escaping needed at all

        # The config parses and the child resolves back.
        resolved = [c.resolve() for c in resolve_children(parent)]
        assert child.resolve() in resolved
