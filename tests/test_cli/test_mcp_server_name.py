# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The MCP server is registered as "cgh". Fresh setups write that
#              key for every client; a config an older cgh wrote under
#              "codegraph" moves to "cgh" with its settings and never keeps
#              both; Claude allow rules follow; hooks read either tool
#              prefix; doctor reports a repo still on the old name.

from __future__ import annotations

import argparse
import json
import shutil

import pytest

from codegraph.integrations import mcp_name

# Where each client's MCP config lives; claude, codex and gemini share one.
CLIENT_FILES = {
    "claude": ".mcp.json",
    "codex": ".mcp.json",
    "gemini": ".mcp.json",
    "cursor": ".cursor/mcp.json",
    "bob": ".bob/mcp.json",
}


def _servers(root, rel):
    return json.loads((root / rel).read_text(encoding="utf-8"))["mcpServers"]


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def no_cli_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


# ---------------------------------------------------------------------------
# Fresh setup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(CLIENT_FILES))
def test_fresh_setup_writes_the_cgh_key(tmp_path, no_cli_on_path, tool):
    from codegraph.cli.commands_init import _install_integration

    _install_integration(tmp_path, tool)

    servers = _servers(tmp_path, CLIENT_FILES[tool])
    assert list(servers) == ["cgh"]


def test_fresh_auto_accept_writes_cgh_patterns(tmp_path):
    from codegraph.cli.commands_init import _configure_claude_auto_accept

    added = _configure_claude_auto_accept(tmp_path)

    assert added == ["mcp__cgh", "mcp__cgh__*"]
    local = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    assert local["permissions"]["allow"] == ["mcp__cgh", "mcp__cgh__*"]


def test_server_announces_itself_as_cgh():
    from codegraph.server import mcp

    assert mcp.name == "cgh"


# ---------------------------------------------------------------------------
# Migration of each client format
# ---------------------------------------------------------------------------

LEGACY_ENTRY = {
    "command": "/old/bin/cgh",
    "args": ["serve", "--root", "."],
    "env": {"CGH_DB": "sqlite"},
    "timeout": 300_000,
    "alwaysAllow": ["fetch_and_index"],
}


@pytest.mark.parametrize("rel", sorted(set(CLIENT_FILES.values())))
def test_migrate_repo_moves_the_entry_and_keeps_its_settings(tmp_path, rel):
    _write(
        tmp_path / rel,
        {"mcpServers": {"codegraph": LEGACY_ENTRY, "other": {"command": "x"}}},
    )

    result = mcp_name.migrate_repo(tmp_path)

    assert result.servers == [rel]
    servers = _servers(tmp_path, rel)
    assert sorted(servers) == ["cgh", "other"]
    assert servers["cgh"] == LEGACY_ENTRY
    assert servers["other"] == {"command": "x"}
    # Idempotent: a second run finds nothing to do.
    assert not mcp_name.migrate_repo(tmp_path).changed


@pytest.mark.parametrize("tool", sorted(CLIENT_FILES))
def test_setup_over_a_legacy_config_leaves_one_cgh_entry(
    tmp_path, no_cli_on_path, tool
):
    from codegraph.cli.commands_init import _install_integration

    rel = CLIENT_FILES[tool]
    _write(
        tmp_path / rel,
        {"mcpServers": {"codegraph": LEGACY_ENTRY, "other": {"command": "x"}}},
    )

    _install_integration(tmp_path, tool)

    servers = _servers(tmp_path, rel)
    assert sorted(servers) == ["cgh", "other"]
    entry = servers["cgh"]
    # User settings survive; the command is refreshed by the setup.
    assert entry["env"] == {"CGH_DB": "sqlite"}
    assert entry["timeout"] == 300_000
    assert "fetch_and_index" in entry["alwaysAllow"]
    assert entry["command"] != "/old/bin/cgh"


def test_both_keys_collapse_to_one_and_cgh_wins(tmp_path):
    _write(
        tmp_path / ".mcp.json",
        {
            "mcpServers": {
                "codegraph": {"command": "old", "env": {"A": "1"}},
                "cgh": {"command": "new"},
            }
        },
    )

    mcp_name.migrate_repo(tmp_path)

    servers = _servers(tmp_path, ".mcp.json")
    assert servers == {"cgh": {"command": "new", "env": {"A": "1"}}}


def test_unreadable_config_is_left_alone(tmp_path):
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")

    assert not mcp_name.migrate_repo(tmp_path).changed
    assert (tmp_path / ".mcp.json").read_text() == "{not json"


# ---------------------------------------------------------------------------
# Claude settings: allow rules and server name lists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_allow_rules_renamed_and_restrictions_twinned(tmp_path, name):
    path = tmp_path / ".claude" / name
    _write(
        path,
        {
            "permissions": {
                "allow": [
                    "Bash(git status:*)",
                    "mcp__codegraph",
                    "mcp__codegraph__*",
                    "mcp__codegraph__symbol_lookup",
                    "mcp__cgh__*",
                    "mcp__codegraphx__foo",
                    "mcp__other__codegraph",
                ],
                "deny": ["mcp__codegraph__scan_repo"],
            },
            "enabledMcpjsonServers": ["codegraph", "other"],
            "disabledMcpjsonServers": ["codegraph"],
            "hooks": {"PreToolUse": [{"matcher": "mcp__(cgh|codegraph)__.*"}]},
        },
    )

    result = mcp_name.migrate_repo(tmp_path)

    assert result.settings == [f".claude/{name}"]
    data = json.loads(path.read_text())
    assert data["permissions"]["allow"] == [
        "Bash(git status:*)",
        "mcp__cgh",
        "mcp__cgh__*",
        "mcp__cgh__symbol_lookup",
        "mcp__codegraphx__foo",
        "mcp__other__codegraph",
    ]
    # A restriction is never dropped: the old deny rule stays, with a twin.
    assert data["permissions"]["deny"] == [
        "mcp__codegraph__scan_repo",
        "mcp__cgh__scan_repo",
    ]
    assert data["enabledMcpjsonServers"] == ["cgh", "other"]
    assert data["disabledMcpjsonServers"] == ["codegraph", "cgh"]
    # A matcher that already fires for both names is left as it is.
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "mcp__(cgh|codegraph)__.*"


def test_auto_accept_over_legacy_rules_keeps_only_cgh(tmp_path):
    from codegraph.cli.commands_init import _configure_claude_auto_accept

    path = tmp_path / ".claude" / "settings.local.json"
    _write(
        path,
        {"permissions": {"allow": ["mcp__codegraph", "mcp__codegraph__*", "Read(*)"]}},
    )

    _configure_claude_auto_accept(tmp_path)

    allow = json.loads(path.read_text())["permissions"]["allow"]
    assert allow == ["mcp__cgh", "mcp__cgh__*", "Read(*)"]


# ---------------------------------------------------------------------------
# Hooks accept both prefixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mcp__cgh__checkpoint", "checkpoint"),
        ("mcp__codegraph__checkpoint", "checkpoint"),
        ("mcp__other__checkpoint", None),
        ("mcp__cgh__", None),
        ("Read", None),
    ],
)
def test_cgh_tool_name_reads_both_prefixes(name, expected):
    assert mcp_name.cgh_tool_name(name) == expected


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__cgh__checkpoint", {}),
        ("mcp__codegraph__checkpoint", {}),
        ("use_mcp_tool", {"server_name": "cgh", "tool_name": "checkpoint"}),
        ("use_mcp_tool", {"server_name": "codegraph", "tool_name": "checkpoint"}),
    ],
)
def test_bob_hooks_count_a_save_under_either_prefix(tool, tool_input):
    from codegraph.cli.commands_bob import _is_save

    assert _is_save(tool, tool_input)


def test_bob_hooks_ignore_other_servers():
    from codegraph.cli.commands_bob import _is_save

    assert not _is_save("mcp__other__checkpoint", {})


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def test_legacy_findings_lists_what_migrate_would_change(tmp_path):
    _write(tmp_path / ".bob" / "mcp.json", {"mcpServers": {"codegraph": {}}})
    _write(
        tmp_path / ".claude" / "settings.json",
        {"permissions": {"allow": ["mcp__codegraph__*"]}},
    )

    found = mcp_name.legacy_findings(tmp_path)

    assert (".bob/mcp.json", 'server key "codegraph"') in found
    assert any(rel == ".claude/settings.json" for rel, _w in found)


def test_legacy_findings_empty_on_a_migrated_repo(tmp_path):
    _write(tmp_path / ".mcp.json", {"mcpServers": {"cgh": {}}})
    _write(
        tmp_path / ".claude" / "settings.local.json",
        {"permissions": {"allow": ["mcp__cgh__*"]}},
    )

    assert mcp_name.legacy_findings(tmp_path) == []


def _doctor_output(tmp_path, monkeypatch, capsys) -> str:
    from codegraph.cli import commands_monitor

    monkeypatch.setattr(commands_monitor.console, "width", 250)
    commands_monitor.cmd_doctor(argparse.Namespace(root=str(tmp_path), strict=False))
    return capsys.readouterr().out


def test_doctor_flags_the_old_key_with_the_fix_command(tmp_path, monkeypatch, capsys):
    _write(tmp_path / ".mcp.json", {"mcpServers": {"codegraph": {}}})

    out = _doctor_output(tmp_path, monkeypatch, capsys)

    row = next(line for line in out.splitlines() if "MCP server name" in line)
    assert "cgh setup migrate" in row
    assert ".mcp.json" in row
    audit = next(line for line in out.splitlines() if "(cgh entry)" in line)
    assert "codegraph" in audit


def test_doctor_is_quiet_on_a_migrated_repo(tmp_path, monkeypatch, capsys):
    _write(tmp_path / ".mcp.json", {"mcpServers": {"cgh": {}}})

    out = _doctor_output(tmp_path, monkeypatch, capsys)

    row = next(line for line in out.splitlines() if "MCP server name" in line)
    assert "OK" in row
    assert "cgh setup migrate" not in out


def test_doctor_strict_does_not_block_on_the_old_key(tmp_path, monkeypatch, capsys):
    from codegraph.cli import commands_monitor

    _write(tmp_path / ".mcp.json", {"mcpServers": {"codegraph": {}}})
    monkeypatch.setattr(commands_monitor.console, "width", 250)
    try:
        commands_monitor.cmd_doctor(argparse.Namespace(root=str(tmp_path), strict=True))
    except SystemExit:
        pass
    out = capsys.readouterr().out
    strict = [line for line in out.splitlines() if line.startswith("strict:")]
    assert all("MCP server name" not in line for line in strict)


# ---------------------------------------------------------------------------
# cgh setup migrate
# ---------------------------------------------------------------------------


def test_setup_migrate_renames_without_installing_anything(tmp_path):
    from codegraph.cli.commands_init import cmd_setup

    _write(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {"codegraph": {"a": 1}}})

    cmd_setup(argparse.Namespace(root=str(tmp_path), target="migrate"))

    assert _servers(tmp_path, ".cursor/mcp.json") == {"cgh": {"a": 1}}
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


# ---------------------------------------------------------------------------
# Fail closed: what the rename would void stops it
# ---------------------------------------------------------------------------


def _legacy_repo(root):
    _write(root / ".mcp.json", {"mcpServers": {"codegraph": {"command": "cgh"}}})


@pytest.mark.parametrize("kind", ["deny", "ask"])
def test_project_deny_and_ask_rules_keep_the_old_and_gain_a_twin(tmp_path, kind):
    _legacy_repo(tmp_path)
    path = tmp_path / ".claude" / "settings.local.json"
    _write(path, {"permissions": {kind: ["mcp__codegraph__scan_repo", "Bash(rm:*)"]}})

    result = mcp_name.migrate_repo(tmp_path)

    assert not result.blocked
    rules = json.loads(path.read_text())["permissions"][kind]
    assert rules == ["mcp__codegraph__scan_repo", "mcp__cgh__scan_repo", "Bash(rm:*)"]
    assert list(_servers(tmp_path, ".mcp.json")) == ["cgh"]


@pytest.mark.parametrize(
    "layer",
    [
        {"permissions": {"deny": ["mcp__codegraph__scan_repo"]}},
        {"permissions": {"ask": ["mcp__codegraph__*"]}},
        {"disabledMcpjsonServers": ["codegraph"]},
        {"hooks": {"PreToolUse": [{"matcher": "mcp__codegraph__scan_repo"}]}},
    ],
    ids=["deny", "ask", "disabled-server", "hook"],
)
def test_user_layer_restriction_blocks_the_rename(tmp_path, layer):
    _legacy_repo(tmp_path)
    before = (tmp_path / ".mcp.json").read_text()
    _write(mcp_name._home() / ".claude" / "settings.json", layer)

    result = mcp_name.migrate_repo(tmp_path)

    assert result.blocked
    assert result.blockers[0].file == "~/.claude/settings.json"
    assert (tmp_path / ".mcp.json").read_text() == before


def test_managed_settings_deny_blocks_the_rename(tmp_path):
    _legacy_repo(tmp_path)
    managed = mcp_name.managed_settings_paths()[0]
    _write(managed, {"permissions": {"deny": ["mcp__codegraph__fetch_and_index"]}})

    result = mcp_name.migrate_repo(tmp_path)

    assert result.blocked
    assert result.blockers[0].file == str(managed)
    assert list(_servers(tmp_path, ".mcp.json")) == ["codegraph"]


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_project_hook_on_the_old_names_blocks_the_rename(tmp_path, name):
    _legacy_repo(tmp_path)
    _write(
        tmp_path / ".claude" / name,
        {"hooks": {"PreToolUse": [{"matcher": "mcp__codegraph__scan_repo"}]}},
    )

    result = mcp_name.migrate_repo(tmp_path)

    assert result.blocked
    assert "scan_repo" in result.blockers[0].what
    assert list(_servers(tmp_path, ".mcp.json")) == ["codegraph"]


@pytest.mark.parametrize(
    ("matcher", "blocks"),
    [
        ("mcp__codegraph__scan_repo", True),
        ("mcp__codegraph__.*", True),
        ("mcp__codegraph__(", True),  # cannot compile: assumed to block
        ("mcp__(cgh|codegraph)__scan_repo", False),
        ("mcp__(cgh|codegraph)__.*", False),
        ("mcp__.*", False),
        ("Bash", False),
    ],
)
def test_hook_matcher_blocks_only_when_it_misses_cgh(matcher, blocks):
    assert mcp_name._matcher_misses_cgh(matcher) is blocks


@pytest.mark.parametrize("where", ["project", "project-local", "user", "managed"])
def test_unreadable_settings_layer_blocks_the_rename(tmp_path, where):
    _legacy_repo(tmp_path)
    path = {
        "project": tmp_path / ".claude" / "settings.json",
        "project-local": tmp_path / ".claude" / "settings.local.json",
        "user": mcp_name._home() / ".claude" / "settings.json",
        "managed": mcp_name.managed_settings_paths()[0],
    }[where]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    result = mcp_name.migrate_repo(tmp_path)

    assert result.blocked
    assert "cannot be read" in result.blockers[0].what
    assert list(_servers(tmp_path, ".mcp.json")) == ["codegraph"]
    assert path.read_text() == "{ not json"


@pytest.mark.parametrize("tool", sorted(CLIENT_FILES))
def test_setup_keeps_a_blocked_repo_on_codegraph(tmp_path, no_cli_on_path, tool):
    from codegraph.cli.commands_init import _install_integration

    rel = CLIENT_FILES[tool]
    _write(tmp_path / rel, {"mcpServers": {"codegraph": {"env": {"A": "1"}}}})
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"permissions": {"deny": ["mcp__codegraph__scan_repo"]}},
    )

    _install_integration(tmp_path, tool)

    servers = _servers(tmp_path, rel)
    assert list(servers) == ["codegraph"]
    assert servers["codegraph"]["env"] == {"A": "1"}


def test_blocked_auto_accept_keeps_the_codegraph_wildcards(tmp_path):
    from codegraph.cli.commands_init import _configure_claude_auto_accept

    _legacy_repo(tmp_path)
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"hooks": {"PreToolUse": [{"matcher": "mcp__codegraph__scan_repo"}]}},
    )

    added = _configure_claude_auto_accept(tmp_path)

    assert added == ["mcp__codegraph", "mcp__codegraph__*"]


def test_auto_accept_never_rewrites_an_unreadable_local_settings(tmp_path):
    from codegraph.cli.commands_init import _configure_claude_auto_accept

    path = tmp_path / ".claude" / "settings.local.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"permissions": {"deny": ["Bash(rm:*)"]', encoding="utf-8")

    assert _configure_claude_auto_accept(tmp_path) == []
    assert path.read_text() == '{"permissions": {"deny": ["Bash(rm:*)"]'


def test_setup_migrate_blocked_exits_1_and_changes_nothing(tmp_path, capsys):
    from codegraph.cli.commands_init import cmd_setup

    _legacy_repo(tmp_path)
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"permissions": {"deny": ["mcp__codegraph__scan_repo"]}},
    )

    with pytest.raises(SystemExit) as exc:
        cmd_setup(argparse.Namespace(root=str(tmp_path), target="migrate", force=False))

    assert exc.value.code == 1
    assert list(_servers(tmp_path, ".mcp.json")) == ["codegraph"]
    out = capsys.readouterr().out
    assert "~/.claude/settings.json" in out
    assert "mcp__codegraph__scan_repo" in out


def test_setup_migrate_force_renames_with_a_warning(tmp_path, capsys):
    from codegraph.cli.commands_init import cmd_setup

    _legacy_repo(tmp_path)
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"permissions": {"deny": ["mcp__codegraph__scan_repo"]}},
    )

    cmd_setup(argparse.Namespace(root=str(tmp_path), target="migrate", force=True))

    assert list(_servers(tmp_path, ".mcp.json")) == ["cgh"]
    assert "--force" in capsys.readouterr().out


def test_init_step_leaves_a_blocked_repo_alone(tmp_path, capsys):
    from codegraph.cli.commands_init import migrate_mcp_server_name

    _legacy_repo(tmp_path)
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"permissions": {"ask": ["mcp__codegraph__scan_repo"]}},
    )

    migrate_mcp_server_name(tmp_path)

    assert list(_servers(tmp_path, ".mcp.json")) == ["codegraph"]
    assert 'left on "codegraph"' in capsys.readouterr().out


def test_doctor_reports_a_blocker_as_a_strict_failure(tmp_path, monkeypatch, capsys):
    from codegraph.cli import commands_monitor

    _legacy_repo(tmp_path)
    _write(
        mcp_name._home() / ".claude" / "settings.json",
        {"permissions": {"deny": ["mcp__codegraph__scan_repo"]}},
    )
    monkeypatch.setattr(commands_monitor.console, "width", 250)

    with pytest.raises(SystemExit):
        commands_monitor.cmd_doctor(argparse.Namespace(root=str(tmp_path), strict=True))

    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "MCP server name" in line)
    assert "rename blocked" in row
    strict = next(line for line in out.splitlines() if line.startswith("strict:"))
    assert "MCP server name" in strict
    assert "mcp__codegraph__scan_repo" in out
