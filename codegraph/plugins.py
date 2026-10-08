# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Plugin discovery and loading.
#              Discovers pip-installed plugins through the "cgh" entry
#              point group, checks CGH_PLUGIN_API against API_VERSION,
#              applies the [plugins] enabled/disabled config, calls each
#              plugin's register(api), and records per-plugin status
#              (active / disabled / incompatible / broken) for
#              `cgh plugins`. First-party plugins older than the minimum
#              this core needs are reported broken and never imported.
#              A broken plugin is a warning, never a crash. Loading is
#              once per process.

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from codegraph.plugin_api import API_VERSION, PluginAPI, _Registries

_ENTRY_POINT_GROUP = "cgh"

# The core series the minimums below were set for, used in the message.
_CORE_SERIES = "0.15"

# First-party plugins this core refuses below a version: older releases
# rely on behavior 0.15 removed. cgh-summarize < 0.3.0 gated `claude -p`
# egress on secure mode, which is now ignored, so it would send code with
# no gate; cgh-pii < 0.4.0 and cgh-vision < 0.6.0 scan on every index;
# cgh-classify < 0.2.0 fed the removed egress gate. Keyed by normalized
# distribution name, so a third-party plugin is never matched.
_FIRST_PARTY_MINIMUMS: dict[str, tuple[int, ...]] = {
    "cgh-pii": (0, 4, 0),
    "cgh-summarize": (0, 3, 0),
    "cgh-classify": (0, 2, 0),
    "cgh-vision": (0, 6, 0),
}

_UPGRADE_HINT = 'uv tool install --force -U "cgh[plugins]"'


@dataclass
class LoadedPlugin:
    """Status record for one discovered plugin."""

    name: str
    status: str  # "active" | "disabled" | "incompatible" | "broken" | "duplicate"
    version: str = ""  # distribution version, best effort
    api_version: int | None = None
    surfaces: list[str] = field(default_factory=list)
    reason: str = ""  # populated for non-active statuses
    too_old: bool = False  # broken because below this core's first-party minimum


_loaded: dict[str, LoadedPlugin] | None = None
_registries = _Registries()


def _iter_entry_points():
    """Indirection over importlib.metadata for testability."""
    from importlib import metadata

    return list(metadata.entry_points(group=_ENTRY_POINT_GROUP))


def _dist_version(entry_point) -> str:
    try:
        dist = getattr(entry_point, "dist", None)
        return dist.version if dist is not None else ""
    except Exception:
        return ""


def _dist_name(entry_point) -> str:
    """Normalized distribution name (PEP 503), "" when unknown."""
    try:
        dist = getattr(entry_point, "dist", None)
        name = dist.metadata["Name"] if dist is not None else ""
    except Exception:
        return ""
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def _release_tuple(version: str) -> tuple[int, ...] | None:
    """Leading numeric release segment ("0.3.0rc1" gives (0, 3, 0)), None
    when the version does not start with one."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def too_old_reason(dist_name: str, version: str) -> str:
    """Why this core refuses a first-party plugin version, or "" when it
    does not (third-party plugin, recent enough, or unknown version)."""
    minimum = _FIRST_PARTY_MINIMUMS.get(dist_name)
    if minimum is None:
        return ""
    have = _release_tuple(version)
    if have is None:
        return ""
    width = max(len(have), len(minimum))
    if have + (0,) * (width - len(have)) >= minimum + (0,) * (width - len(minimum)):
        return ""
    needed = ".".join(str(part) for part in minimum)
    return (
        f"{dist_name} {version} is too old for cgh {_CORE_SERIES} "
        f"(needs >= {needed}); upgrade: {_UPGRADE_HINT} "
        f"(add --with {dist_name} to keep it)"
    )


def load_plugins(repo_root: str | Path | None = None) -> list[LoadedPlugin]:
    """Discover and load every installed plugin. Idempotent per process:
    the first call does the work, later calls return the same records.

    ``repo_root`` resolves the [plugins] enabled/disabled config and the
    per-plugin [plugin.<name>] tables; None loads with global config
    only (repo-less commands like `cgh parsers`).
    """
    global _loaded
    if _loaded is not None:
        return list(_loaded.values())
    _loaded = {}

    from codegraph.core.config import load_config

    cfg = load_config(repo_root)
    enabled = cfg.plugins_enabled  # None = no allowlist
    disabled = set(cfg.plugins_disabled)

    for ep in _iter_entry_points():
        name = ep.name
        if name in _loaded:
            _loaded[f"{name}#dup"] = LoadedPlugin(
                name=name,
                status="duplicate",
                version=_dist_version(ep),
                reason="another plugin already registered this name",
            )
            continue

        record = LoadedPlugin(name=name, status="active", version=_dist_version(ep))
        _loaded[name] = record

        if name in disabled:
            record.status = "disabled"
            record.reason = "listed in [plugins] disabled"
            continue
        if enabled is not None and name not in enabled:
            record.status = "disabled"
            record.reason = "not in [plugins] enabled allowlist"
            continue

        # Checked before import: a stale first-party plugin must not run
        # any of its code, not even module-level code.
        stale = too_old_reason(_dist_name(ep), record.version)
        if stale:
            record.status = "broken"
            record.too_old = True
            record.reason = stale
            _warn(f"plugin {name}: {stale}")
            continue

        try:
            module = ep.load()
        except (Exception, SystemExit) as exc:
            # A plugin built against an older cgh may import a name core
            # has since removed; that is a broken plugin, never a dead CLI.
            record.status = "broken"
            record.reason = f"import failed: {type(exc).__name__}: {exc}"
            _warn(f"plugin {name}: {record.reason}")
            continue

        declared = getattr(module, "CGH_PLUGIN_API", None)
        record.api_version = declared
        if declared != API_VERSION:
            record.status = "incompatible"
            record.reason = (
                f"declares CGH_PLUGIN_API={declared!r}, this cgh provides {API_VERSION}"
            )
            _warn(f"plugin {name}: {record.reason}")
            continue

        register = getattr(module, "register", None)
        if not callable(register):
            record.status = "broken"
            record.reason = "module has no callable register(api)"
            _warn(f"plugin {name}: {record.reason}")
            continue

        api = PluginAPI(
            plugin_name=name,
            repo_root=Path(repo_root).resolve() if repo_root else None,
            config=cfg.plugin_tables.get(name, {}),
            registries=_registries,
        )
        try:
            register(api)
        except (Exception, SystemExit) as exc:
            # Drop whatever the plugin registered before it failed, so a
            # half-registered plugin cannot leave a CLI verb or a scanner
            # behind while `cgh plugins` reports it broken. Parsers go
            # through the global parser table and are not rolled back.
            _drop_registrations(name)
            record.status = "broken"
            record.reason = f"register() raised: {type(exc).__name__}: {exc}"
            _warn(f"plugin {name}: {record.reason}")
            continue

        record.surfaces = api.surfaces

    return list(_loaded.values())


def _drop_registrations(plugin_name: str) -> None:
    reg = _registries
    reg.scanners[:] = [e for e in reg.scanners if e[0] != plugin_name]
    reg.mcp_registrars[:] = [e for e in reg.mcp_registrars if e[0] != plugin_name]
    reg.cli_registrars[:] = [e for e in reg.cli_registrars if e[0] != plugin_name]
    for namespace, entries in list(reg.extensions.items()):
        kept = [e for e in entries if e[0] != plugin_name]
        if kept:
            reg.extensions[namespace] = kept
        else:
            del reg.extensions[namespace]


def loaded_plugins() -> list[LoadedPlugin]:
    """Records from the last load; empty if load_plugins never ran."""
    return list(_loaded.values()) if _loaded else []


def cli_registrars() -> list[tuple[str, object]]:
    return list(_registries.cli_registrars)


def mcp_registrars() -> list[tuple[str, object]]:
    return list(_registries.mcp_registrars)


def scanners() -> list[tuple[str, object]]:
    return list(_registries.scanners)


def get_extensions(namespace: str) -> list[object]:
    """Objects published under ``namespace``, in registration order."""
    return [obj for _, obj in _registries.extensions.get(namespace, [])]


def extension_entries(namespace: str) -> list[tuple[str, object]]:
    """``(plugin_name, obj)`` pairs published under ``namespace``."""
    return list(_registries.extensions.get(namespace, []))


def _warn(message: str) -> None:
    print(f"[codegraph] {message}", file=sys.stderr, flush=True)


def _reset_for_tests() -> None:
    """Drop all load state. Test helper, never called in production."""
    global _loaded, _registries
    _loaded = None
    _registries = _Registries()
