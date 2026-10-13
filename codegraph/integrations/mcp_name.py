# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The MCP server name cgh registers under ("cgh"), the legacy
#              name older versions wrote ("codegraph"), and the migration
#              from one to the other. The migration fails closed:
#                rename_blockers        restrictions the rename would void
#                                       (deny / ask rules and hook matchers
#                                       naming mcp__codegraph in a layer cgh
#                                       does not manage, or an unreadable
#                                       layer); any one stops the rename
#                migrate_repo           the rename itself, when not blocked
#                migrate_servers        one mcpServers mapping
#                migrate_settings_data  one Claude settings mapping: allow
#                                       rules renamed, deny / ask rules gain
#                                       their mcp__cgh twin and keep the old
#                server_key_for         the key a setup writes, given blockers
#                legacy_findings        what is still left to migrate
#                cgh_tool_name          tool name under either prefix

from __future__ import annotations

import json
import os
import re
import sys
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
LEGACY_ALLOW_PATTERNS = (f"mcp__{LEGACY_SERVER_KEY}", f"{LEGACY_TOOL_PREFIX}*")

# The client configs cgh writes an MCP server entry into, relative to the
# repo root: Claude, Codex and Gemini share .mcp.json.
MCP_CONFIG_FILES = (".mcp.json", ".cursor/mcp.json", ".bob/mcp.json")
# The Claude settings files cgh manages, relative to the repo root.
CLAUDE_SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")

# The one command that migrates a repo, quoted by doctor and the docs.
MIGRATE_COMMAND = "cgh setup migrate"
UPGRADING_DOC = "docs/UPGRADING-0.19.md"

# Exactly the permission entries an older cgh (or the "don't ask again"
# dialog) wrote: the bare server rule, the wildcard, or one tool name.
_LEGACY_RULE = re.compile(rf"^mcp__{LEGACY_SERVER_KEY}(__(\*|[A-Za-z0-9_-]+))?$")
_RESTRICTION_LISTS = ("deny", "ask")
_SERVER_RESTRICTION_LISTS = ("disabledMcpjsonServers",)
_TOOL_IN_MATCHER = re.compile(rf"{LEGACY_TOOL_PREFIX}([A-Za-z0-9_]+)")


def cgh_tool_name(name: str) -> str | None:
    """The bare tool name behind an MCP tool id under either prefix, or
    None for a tool that is not cgh's. Hooks use this so a session started
    before the rename keeps working."""
    for prefix in (TOOL_PREFIX, LEGACY_TOOL_PREFIX):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :]
    return None


# ---------------------------------------------------------------------------
# Settings layers
# ---------------------------------------------------------------------------


def _home() -> Path:
    return Path.home()


def managed_settings_paths() -> list[Path]:
    """Where Claude Code reads an administrator's managed settings."""
    if sys.platform == "darwin":
        return [Path("/Library/Application Support/ClaudeCode/managed-settings.json")]
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [Path(base) / "ClaudeCode" / "managed-settings.json"]
    return [Path("/etc/claude-code/managed-settings.json")]


def settings_layers(root: Path) -> list[tuple[Path, str, bool]]:
    """Every Claude settings file cgh can read for this repo, as
    (path, label, managed_by_cgh)."""
    layers: list[tuple[Path, str, bool]] = [
        (root / rel, rel, True) for rel in CLAUDE_SETTINGS_FILES
    ]
    home = _home()
    for name in ("settings.json", "settings.local.json"):
        path = home / ".claude" / name
        layers.append((path, f"~/.claude/{name}", False))
    for path in managed_settings_paths():
        layers.append((path, str(path), False))
    return layers


@dataclass(frozen=True, slots=True)
class Blocker:
    """One reason the rename would drop a restriction."""

    file: str
    what: str
    fix: str

    def __str__(self) -> str:
        return f"{self.file}: {self.what}. Fix: {self.fix}"


def _matcher_misses_cgh(matcher: str) -> bool:
    """True when a hook matcher fires for some mcp__codegraph tool but not
    for the same tool under mcp__cgh, so the rename would silence it. A
    matcher that cannot be compiled and still mentions codegraph counts as
    missing: fail closed."""
    if LEGACY_SERVER_KEY not in matcher:
        return False
    tools = set(_TOOL_IN_MATCHER.findall(matcher)) or {"checkpoint"}
    try:
        pattern = re.compile(matcher)
    except re.error:
        return True
    for tool in tools:
        legacy = pattern.fullmatch(f"{LEGACY_TOOL_PREFIX}{tool}") is not None
        legacy = legacy or matcher == f"{LEGACY_TOOL_PREFIX}{tool}"
        current = pattern.fullmatch(f"{TOOL_PREFIX}{tool}") is not None
        if legacy and not current:
            return True
    return False


def _legacy_hook_matchers(data: dict) -> list[str]:
    found: list[str] = []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return found
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            matcher = group.get("matcher") if isinstance(group, dict) else None
            if isinstance(matcher, str) and _matcher_misses_cgh(matcher):
                found.append(matcher)
    return found


def _legacy_restrictions(data: dict) -> list[str]:
    """deny / ask rules and server restrictions that name codegraph."""
    found: list[str] = []
    perms = data.get("permissions")
    if isinstance(perms, dict):
        for key in _RESTRICTION_LISTS:
            for rule in perms.get(key) or []:
                if isinstance(rule, str) and rule.startswith(
                    f"mcp__{LEGACY_SERVER_KEY}"
                ):
                    found.append(f"permissions.{key} {rule}")
    for key in _SERVER_RESTRICTION_LISTS:
        if LEGACY_SERVER_KEY in (data.get(key) or []):
            found.append(f'{key} "{LEGACY_SERVER_KEY}"')
    for entry in data.get("deniedMcpServers") or []:
        if isinstance(entry, dict) and entry.get("serverName") == LEGACY_SERVER_KEY:
            found.append(f'deniedMcpServers "{LEGACY_SERVER_KEY}"')
    return found


def _read_layer(path: Path) -> tuple[dict | None, str | None]:
    """(data, error). A missing file is ({}, None); anything that exists
    but cannot be read as a JSON object is an error."""
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def rename_blockers(root: Path) -> list[Blocker]:
    """Everything that makes the rename unsafe. The rename moves the tools
    from mcp__codegraph__* to mcp__cgh__*, so any rule or hook keyed on the
    old names would silently stop applying:

    - a settings layer that exists but cannot be read (it may hold such
      a rule, so it is assumed to);
    - a hook matcher, in any layer, that fires for mcp__codegraph tools
      and not for their mcp__cgh twins (cgh never edits team hooks);
    - a deny / ask rule or a server restriction naming codegraph in a
      layer cgh does not manage (the user or managed settings).

    deny / ask rules in the two project files cgh manages do not block:
    the migration adds the mcp__cgh twin and keeps the original.
    """
    blockers: list[Blocker] = []
    for path, label, managed in settings_layers(root):
        data, error = _read_layer(path)
        if error is not None:
            blockers.append(
                Blocker(
                    label,
                    f"cannot be read ({error}), so its rules cannot be checked",
                    "fix or remove the file, then run the migration again",
                )
            )
            continue
        if data is None:  # unreachable: _read_layer pairs None with an error
            continue
        for matcher in _legacy_hook_matchers(data):
            blockers.append(
                Blocker(
                    label,
                    f'hook matcher "{matcher}" would stop firing for the mcp__cgh tools',
                    "make it match both, e.g. mcp__(cgh|codegraph)__<tool>",
                )
            )
        if managed:
            continue
        for rule in _legacy_restrictions(data):
            blockers.append(
                Blocker(
                    label,
                    f"{rule} would stop applying to the mcp__cgh tools",
                    "add the same rule for mcp__cgh (or cgh) next to it",
                )
            )
    return blockers


# ---------------------------------------------------------------------------
# Rewrites
# ---------------------------------------------------------------------------


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


def server_key_for(servers: dict, blocked: bool) -> str:
    """The key a setup writes the server under. Unblocked: "cgh". Blocked:
    the repo stays on "codegraph", unless it is already on "cgh" alone
    (cgh never renames back)."""
    if not blocked:
        return SERVER_KEY
    if SERVER_KEY in servers and LEGACY_SERVER_KEY not in servers:
        return SERVER_KEY
    return LEGACY_SERVER_KEY


def rewrite_rule(rule: str) -> str:
    """mcp__codegraph[__x] to mcp__cgh[__x]; any other rule unchanged."""
    if isinstance(rule, str) and _LEGACY_RULE.match(rule):
        return f"mcp__{SERVER_KEY}" + rule[len(f"mcp__{LEGACY_SERVER_KEY}") :]
    return rule


def rewrite_permission_list(rules: list) -> bool:
    """allow rules: rename legacy cgh rules in place, dropping the
    duplicates the rewrite creates. Returns True when the list changed."""
    out: list = []
    for rule in rules:
        new = rewrite_rule(rule)
        if new not in out:
            out.append(new)
    if out == rules:
        return False
    rules[:] = out
    return True


def twin_restriction_list(rules: list) -> bool:
    """deny / ask rules: give every legacy cgh rule its mcp__cgh twin,
    right after it, and keep the original. A restriction is never
    dropped. Returns True when the list changed."""
    out: list = []
    for rule in rules:
        if rule not in out:
            out.append(rule)
        twin = rewrite_rule(rule)
        if twin != rule and twin not in rules and twin not in out:
            out.append(twin)
    if out == rules:
        return False
    rules[:] = out
    return True


def migrate_settings_data(data: dict) -> bool:
    """Migrate one Claude settings mapping cgh manages: allow rules are
    renamed, deny / ask rules gain their mcp__cgh twin and keep the old,
    "codegraph" in enabledMcpjsonServers is renamed and in
    disabledMcpjsonServers gains "cgh" next to it."""
    changed = False
    perms = data.get("permissions")
    if isinstance(perms, dict):
        if isinstance(perms.get("allow"), list):
            changed |= rewrite_permission_list(perms["allow"])
        for key in _RESTRICTION_LISTS:
            if isinstance(perms.get(key), list):
                changed |= twin_restriction_list(perms[key])
    enabled = data.get("enabledMcpjsonServers")
    if isinstance(enabled, list) and LEGACY_SERVER_KEY in enabled:
        new: list = []
        for name in enabled:
            name = SERVER_KEY if name == LEGACY_SERVER_KEY else name
            if name not in new:
                new.append(name)
        data["enabledMcpjsonServers"] = new
        changed = True
    disabled = data.get("disabledMcpjsonServers")
    if (
        isinstance(disabled, list)
        and LEGACY_SERVER_KEY in disabled
        and SERVER_KEY not in disabled
    ):
        disabled.append(SERVER_KEY)
        changed = True
    return changed


def _load(path: Path) -> dict | None:
    data, error = _read_layer(path)
    return None if error is not None else data


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
    blockers: list[Blocker] = field(default_factory=list)
    forced: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.servers or self.settings)

    @property
    def blocked(self) -> bool:
        return bool(self.blockers) and not self.forced


def migrate_repo(root: Path, force: bool = False) -> MigrationResult:
    """Run the whole rename on one repo, or nothing at all when a blocker
    stands and force is off. The project settings are migrated before the
    server keys, so a deny rule's mcp__cgh twin exists by the time the
    tools carry that name. Idempotent."""
    result = MigrationResult(blockers=rename_blockers(root), forced=force)
    if result.blocked:
        return result
    for rel in CLAUDE_SETTINGS_FILES:
        if migrate_claude_settings(root / rel):
            result.settings.append(rel)
    for rel in MCP_CONFIG_FILES:
        if migrate_mcp_file(root / rel):
            result.servers.append(rel)
    return result


def legacy_findings(root: Path) -> list[tuple[str, str]]:
    """What `cgh setup migrate` would still change here, as (file, what)."""
    found: list[tuple[str, str]] = []
    for rel in MCP_CONFIG_FILES:
        data = _load(root / rel) if (root / rel).is_file() else None
        servers = (data or {}).get("mcpServers")
        if isinstance(servers, dict) and LEGACY_SERVER_KEY in servers:
            found.append((rel, f'server key "{LEGACY_SERVER_KEY}"'))
    for rel in CLAUDE_SETTINGS_FILES:
        data = _load(root / rel) if (root / rel).is_file() else None
        if data and migrate_settings_data(json.loads(json.dumps(data))):
            found.append((rel, f"mcp__{LEGACY_SERVER_KEY} permission rules"))
    return found
