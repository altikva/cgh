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
#              `cgh plugins`. A broken plugin is a warning, never a
#              crash. Loading is once per process.

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from codegraph.plugin_api import API_VERSION, PluginAPI, _Registries

_ENTRY_POINT_GROUP = "cgh"


@dataclass
class LoadedPlugin:
    """Status record for one discovered plugin."""

    name: str
    status: str  # "active" | "disabled" | "incompatible" | "broken" | "duplicate"
    version: str = ""  # distribution version, best effort
    api_version: int | None = None
    surfaces: list[str] = field(default_factory=list)
    reason: str = ""  # populated for non-active statuses


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

        try:
            module = ep.load()
        except Exception as exc:
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
        except Exception as exc:
            record.status = "broken"
            record.reason = f"register() raised: {type(exc).__name__}: {exc}"
            _warn(f"plugin {name}: {record.reason}")
            continue

        record.surfaces = api.surfaces

    return list(_loaded.values())


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


def _warn(message: str) -> None:
    print(f"[codegraph] {message}", file=sys.stderr, flush=True)


def _reset_for_tests() -> None:
    """Drop all load state. Test helper, never called in production."""
    global _loaded, _registries
    _loaded = None
    _registries = _Registries()
