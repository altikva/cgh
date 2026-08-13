# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh examples` discovers examples bundled in the base package
#              (and plugins) and installs one locally by copying its files.

from __future__ import annotations

import types

from codegraph.cli.commands_examples import (
    _description,
    cmd_examples,
    discover_examples,
)


def test_description_skips_the_title_heading():
    md = "# my-example\n\nWhat it actually does, in one line.\n"
    assert _description(md) == "What it actually does, in one line."


def test_base_example_is_discovered():
    names = {e["name"] for e in discover_examples()}
    # starter-config ships inside the base codegraph package.
    assert "starter-config" in names
    base = next(e for e in discover_examples() if e["name"] == "starter-config")
    assert base["package"] == "codegraph"
    assert base["description"] and base["description"] != "starter-config"


def test_install_copies_files(tmp_path):
    args = types.SimpleNamespace(
        example_action="install",
        name="starter-config",
        dest=str(tmp_path),
        package="",
        force=False,
    )
    cmd_examples(args)
    dest = tmp_path / "starter-config"
    assert (dest / "README.md").is_file()
    assert (dest / "config.toml").is_file()
    # The config the example ships is real, usable TOML.
    import tomllib

    tomllib.loads((dest / "config.toml").read_text())


def test_install_unknown_name_exits(tmp_path):
    import pytest

    args = types.SimpleNamespace(
        example_action="install",
        name="does-not-exist",
        dest=str(tmp_path),
        package="",
        force=False,
    )
    with pytest.raises(SystemExit):
        cmd_examples(args)
