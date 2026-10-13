# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The MCP server name cgh registers under ("cgh"), the legacy
#              name older versions wrote ("codegraph"), and the migration
#              from one to the other:
#                migrate_servers / migrate_mcp_file   client MCP configs
#                rewrite_permission_list              Claude allow / ask / deny
#                migrate_claude_settings              .claude/settings*.json
#                migrate_repo                         every file above, one call
#                legacy_findings                      what doctor reports
#                cgh_tool_name                        tool name, either prefix

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SERVER_KEY = "cgh"
LEGACY_SERVER_KEY = "codegraph"

TOOL_PREFIX = f"mcp__{SERVER_KEY}__"
LEGACY_TOOL_PREFIX = f"mcp__{LEGACY_SERVER_KEY}__"

# Wildcards that cover every cgh MCP tool, current and future. The
# two-form shape matches what both older and current Claude Code builds
# accept.
ALLOW_PATTERNS = (f"mcp__{SERVER_KEY}", f"{TOOL_PREFIX}*")

# The client configs cgh writes an MCP server entry into, relative to the
# repo root: Claude, Codex and Gemini share .mcp.json.
MCP_CONFIG_FILES = (".mcp.json", ".cursor/mcp.json", ".bob/mcp.json")
CLAUDE_SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")

# The one command that migrates a repo, quoted by doctor and the docs.
MIGRATE_COMMAND = "cgh setup migrate"

# Exactly the permission entries an older cgh (or the "don't ask again"
# dialog) wrote: the bare server rule, the wildcard, or one tool name.
_LEGACY_RULE = re.compile(rf"^mcp__{LEGACY_SERVER_KEY}(__(\*|[A-Za-z0-9_-]+))?$")
_PERMISSION_LISTS = ("allow", "ask", "deny")
_SERVER_NAME_LISTS = ("enabledMcpjsonServers", "disabledMcpjsonServers")


def cgh_tool_name(name: str) -> str | None:
    """The bare tool name behind an MCP tool id under either prefix, or
    None for a tool that is not cgh's. Hooks use this so a session started
    before the rename keeps working."""
    for prefix in (TOOL_PREFIX, LEGACY_TOOL_PREFIX):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :]
    return None


def migrate_servers(servers: dict) -> bool:
    """Move a "codegraph" entry to "cgh" in an mcpServers mapping, keeping
    its settings. When both exist, the "cgh" values win and the legacy
    entry is dropped: two keys would start two proxies. Returns True when
    the mapping changed."""
    if LEGACY_SERVER_KEY not in servers:
        return False
    legacy = servers.pop(LEGACY_SERVER_KEY)
    current = servers.get(SERVER_KEY)
    if isinstance(legacy, dict) and isinstance(current, dict):
        servers[SERVER_KEY] = {**legacy, **current}
    elif current is None:
        servers[SERVER_KEY] = legacy
    return True


def rewrite_rule(rule: str) -> str:
    """mcp__codegraph[__x] to mcp__cgh[__x]; any other rule unchanged."""
    if isinstance(rule, str) and _LEGACY_RULE.match(rule):
        return f"mcp__{SERVER_KEY}" + rule[len(f"mcp__{LEGACY_SERVER_KEY}") :]
    return rule


def rewrite_permission_list(rules: list) -> bool:
    """Rewrite legacy cgh rules in place, dropping the duplicates the
    rewrite creates. Returns True when the list changed."""
    out: list = []
    for rule in rules:
        new = rewrite_rule(rule)
        if new not in out:
            out.append(new)
    if out == rules:
        return False
    rules[:] = out
    return True


def migrate_settings_data(data: dict) -> bool:
    """Rewrite the legacy entries of one Claude settings mapping:
    permissions allow / ask / deny, and the server name in
    enabledMcpjsonServers / disabledMcpjsonServers."""
    changed = False
    perms = data.get("permissions")
    if isinstance(perms, dict):
        for key in _PERMISSION_LISTS:
            if isinstance(perms.get(key), list):
                changed |= rewrite_permission_list(perms[key])
    for key in _SERVER_NAME_LISTS:
        names = data.get(key)
        if isinstance(names, list) and LEGACY_SERVER_KEY in names:
            new: list = []
            for n in names:
                n = SERVER_KEY if n == LEGACY_SERVER_KEY else n
                if n not in new:
                    new.append(n)
            data[key] = new
            changed = True
    return changed


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def migrate_mcp_file(path: Path) -> bool:
    """Migrate the server key of one client MCP config file. A missing or
    unreadable file is left alone. Returns True when the file was
    rewritten."""
    if not path.is_file():
        return False
    data = _load(path)
    if data is None or not isinstance(data.get("mcpServers"), dict):
        return False
    if not migrate_servers(data["mcpServers"]):
        return False
    _write(path, data)
    return True


def migrate_claude_settings(path: Path) -> bool:
    """Migrate the legacy rules of one .claude/settings*.json file."""
    if not path.is_file():
        return False
    data = _load(path)
    if data is None or not migrate_settings_data(data):
        return False
    _write(path, data)
    return True


@dataclass
class MigrationResult:
    servers: list[str] = field(default_factory=list)
    settings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.servers or self.settings)


def migrate_repo(root: Path) -> MigrationResult:
    """Run the whole rename on one repo. Idempotent: a migrated repo is
    left byte for byte as it is."""
    result = MigrationResult()
    for rel in MCP_CONFIG_FILES:
        if migrate_mcp_file(root / rel):
            result.servers.append(rel)
    for rel in CLAUDE_SETTINGS_FILES:
        if migrate_claude_settings(root / rel):
            result.settings.append(rel)
    return result


def _mentions_legacy_prefix(value: object) -> bool:
    return f"mcp__{LEGACY_SERVER_KEY}" in json.dumps(value)


def legacy_findings(root: Path) -> list[tuple[str, str, bool]]:
    """What still uses the old name, as (file, what, fixable). fixable is
    True when `cgh setup migrate` rewrites it, False for a team-owned
    entry (a hook matcher, say) that must be edited by hand."""
    found: list[tuple[str, str, bool]] = []
    for rel in MCP_CONFIG_FILES:
        data = _load(root / rel) if (root / rel).is_file() else None
        servers = (data or {}).get("mcpServers")
        if isinstance(servers, dict) and LEGACY_SERVER_KEY in servers:
            found.append((rel, f'server key "{LEGACY_SERVER_KEY}"', True))
    for rel in CLAUDE_SETTINGS_FILES:
        data = _load(root / rel) if (root / rel).is_file() else None
        if not data:
            continue
        probe = json.loads(json.dumps(data))
        if migrate_settings_data(probe):
            found.append((rel, f"mcp__{LEGACY_SERVER_KEY} permission rules", True))
        if _mentions_legacy_prefix(probe.get("hooks", {})):
            found.append((rel, f"hook matcher naming mcp__{LEGACY_SERVER_KEY}", False))
    return found
