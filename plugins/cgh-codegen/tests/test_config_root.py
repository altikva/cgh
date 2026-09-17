# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: plugin_config_for_root resolves the [plugin.codegen] table
#              from the requested root, so a --root on the CLI governs the
#              backend and egress posture, not just where the file is written.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.cli import plugin_config_for_root


def test_reads_the_table_from_the_given_root(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "config.toml").write_text(
        '[plugin.codegen]\ncommand = "claude -p --model haiku"\n', encoding="utf-8"
    )
    cfg = plugin_config_for_root(tmp_path, {})
    assert cfg.get("command") == "claude -p --model haiku"


def test_falls_back_when_root_has_no_table(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "config.toml").write_text("", encoding="utf-8")
    fallback = {"command": "fallback"}
    assert plugin_config_for_root(tmp_path, fallback) is fallback


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
