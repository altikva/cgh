# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A repo indexed under the old .codegraph/ state dir migrates in
#              place to .cgh/ on init/index/serve, and is read as-is until it
#              does. The rename is skipped while an owner holds the legacy dir.
from __future__ import annotations

import os

from cgh.core import config


def test_state_dir_for_prefers_cgh_then_legacy_then_default(tmp_path):
    # Nothing yet: the default is .cgh/.
    assert config.state_dir_for(tmp_path) == tmp_path / ".cgh"
    # Only a legacy dir: read it.
    (tmp_path / ".codegraph").mkdir()
    assert config.state_dir_for(tmp_path) == tmp_path / ".codegraph"
    # Both: the new one wins.
    (tmp_path / ".cgh").mkdir()
    assert config.state_dir_for(tmp_path) == tmp_path / ".cgh"


def test_migrate_renames_legacy_dir_in_place(tmp_path):
    legacy = tmp_path / ".codegraph"
    legacy.mkdir()
    (legacy / "graph.duckdb").write_text("x", encoding="utf-8")

    assert config.migrate_state_dir(tmp_path) is True

    assert not legacy.exists()
    assert (tmp_path / ".cgh" / "graph.duckdb").read_text() == "x"
    # Idempotent: a second run finds nothing to do.
    assert config.migrate_state_dir(tmp_path) is False


def test_migrate_is_a_noop_without_a_legacy_dir(tmp_path):
    assert config.migrate_state_dir(tmp_path) is False
    (tmp_path / ".cgh").mkdir()
    assert config.migrate_state_dir(tmp_path) is False


def test_migrate_does_not_overwrite_an_existing_cgh_dir(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".cgh").mkdir()
    (tmp_path / ".cgh" / "keep").write_text("keep", encoding="utf-8")

    assert config.migrate_state_dir(tmp_path) is False

    assert (tmp_path / ".codegraph").exists()
    assert (tmp_path / ".cgh" / "keep").read_text() == "keep"


def test_migrate_is_skipped_while_an_owner_holds_the_legacy_dir(tmp_path):
    legacy = tmp_path / ".codegraph"
    legacy.mkdir()
    # A live owner: its pid is this very process, which is certainly alive.
    (legacy / "owner.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    assert config.migrate_state_dir(tmp_path) is False
    assert legacy.exists()
    assert not (tmp_path / ".cgh").exists()


def test_init_project_migrates_a_legacy_repo(tmp_path):
    legacy = tmp_path / ".codegraph"
    legacy.mkdir()
    (legacy / "graph.duckdb").write_text("x", encoding="utf-8")

    config.init_project(tmp_path)

    assert not legacy.exists()
    assert (tmp_path / ".cgh" / "graph.duckdb").read_text() == "x"
    assert (tmp_path / ".cgh" / "config.toml").exists()


def test_find_cgh_root_discovers_a_legacy_repo(tmp_path):
    from cgh.core import config

    (tmp_path / ".codegraph").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    # From a subdir, the legacy .codegraph/ repo root is found like a .cgh/ one.
    assert config.find_cgh_root(sub) == tmp_path
    # And reading from a legacy repo never conjures an empty .cgh/ beside it.
    assert config.state_dir_for(tmp_path) == tmp_path / ".codegraph"
    assert not (tmp_path / ".cgh").exists()


def test_cmd_path_prints_the_resolved_state_dir(tmp_path, capsys):
    import argparse

    from cgh.cli.commands_index import cmd_path

    (tmp_path / ".codegraph").mkdir()
    cmd_path(argparse.Namespace(root=str(tmp_path), state=True))
    out = capsys.readouterr().out.strip()
    assert out == str((tmp_path / ".codegraph").resolve())
