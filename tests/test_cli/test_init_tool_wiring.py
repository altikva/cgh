# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh init` tool wiring: --tools forces a choice regardless of
#              detection (empty-repo bootstrap), the selection is offered
#              even when nothing is detected, and Bob gets its usage rules.

from __future__ import annotations

import types

from codegraph.cli.commands_init import _select_tools
from codegraph.integrations.skill_installer import install_usage_guidelines

# (name, key, detected)
_ALL = [
    ("Claude Code", "claude", False),
    ("Cursor", "cursor", False),
    ("IBM Bob", "bob", True),
]
_DETECTED = [(n, k) for n, k, d in _ALL if d]


def _args(**kw):
    base = dict(yes=False, tools="")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_tools_flag_forces_choice_regardless_of_detection():
    # cursor is not detected, but --tools wires it anyway.
    keys = _select_tools(_ALL, _DETECTED, _args(tools="claude,cursor"), None)
    assert set(keys) == {"claude", "cursor"}


def test_tools_flag_drops_unknown_names():
    keys = _select_tools(_ALL, _DETECTED, _args(tools="claude,notatool"), None)
    assert keys == ["claude"]


def test_yes_selects_detected_only():
    keys = _select_tools(_ALL, _DETECTED, _args(yes=True), None)
    assert keys == ["bob"]


def test_bob_usage_guidelines_land_in_bob_rules(tmp_path):
    written = install_usage_guidelines(tmp_path, "bob")
    assert written is not None
    rules = tmp_path / ".bob" / "rules" / "00-codegraph-usage.md"
    assert rules.is_file()
    assert "codegraph" in rules.read_text(encoding="utf-8").lower()


def test_bob_mcp_server_uses_absolute_command_and_cwd(tmp_path):
    """Bob is an IDE agent whose GUI process does not inherit the login
    shell PATH, so a bare `cgh` command never spawns. The .bob/mcp.json
    must carry an absolute command and a cwd pinned to the project root."""
    import json
    import os

    from codegraph.cli.commands_init import _install_integration

    _install_integration(tmp_path, "bob")
    entry = json.loads((tmp_path / ".bob" / "mcp.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]["codegraph"]

    assert os.path.isabs(entry["command"]), entry["command"]
    assert entry["cwd"] == str(tmp_path.resolve())
    assert entry["args"][:2] == ["serve", "--root"]
    # Bob's stdio schema is command/args/cwd, no "type" field.
    assert "type" not in entry
