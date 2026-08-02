# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Public plugin API. A plugin module exposes
#              CGH_PLUGIN_API = 1 and register(api: PluginAPI). The API
#              covers five surfaces: file parsers, per-file scanners,
#              MCP tools, CLI subcommands, and the generic namespaced
#              extension registry that lets a plugin extend another
#              plugin (e.g. summarizer backends, agent integrations).
#              This module is public, stability-guarded API: breaking
#              changes here bump API_VERSION. It also re-exports (lazily,
#              PEP 562) the core helpers plugins are allowed to depend
#              on: the finding store, activity log, knowledge record,
#              config resolution, parser lookup, federation children and
#              subprocess hygiene. Anything NOT importable from here is
#              internal and may change without notice; the first-party
#              plugins import exclusively from this module.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from codegraph.parsers.base import FileIndex

# Bump on any breaking change to this module's contracts (signature
# changes, removed hooks, field removals). Additive evolution keeps it.
API_VERSION = 1


@dataclass
class ScanFinding:
    """One fact a scanner attached to a file.

    ``key`` is namespaced by convention (``pii.email``, ``secret.aws_key``,
    ``confidential``, ``summary``). ``line`` 0 means a file-level finding.
    ``severity`` drives the guard and the egress gate: ``block`` findings
    always stop a file at the gate.
    """

    key: str
    value: str
    line: int = 0
    severity: str = "info"  # "info" | "warn" | "block"


@runtime_checkable
class FileScanner(Protocol):
    """Post-parse hook: look at a file's content, return findings.

    ``deferred = False`` runs inline in ``index_file`` and must be fast
    (regex tier). ``deferred = True`` is queued and executed off the hot
    path, keyed by blob SHA (NER tier, model calls); deferred scanners
    receive ``index=None`` since the parse result is no longer around.

    NOTE: registration is available from API v1 so plugins can ship
    scanners today, but core only starts invoking them once the finding
    store lands. Until then registered scanners are visible in
    ``cgh plugins`` and simply not called.
    """

    name: str
    deferred: bool

    def scan(self, path: Path, text: str, index: FileIndex) -> list[ScanFinding]: ...


class PluginAPI:
    """The one supported way into cgh for a plugin.

    Instances are created by the loader, one per plugin, and passed to
    the plugin's ``register(api)``. Every ``register_*`` call lands in
    process-wide registries owned by ``codegraph.plugins``; the loader
    records which surfaces each plugin used for ``cgh plugins``.
    """

    def __init__(
        self,
        plugin_name: str,
        repo_root: Path | None,
        config: dict[str, Any],
        registries: _Registries,
    ) -> None:
        self.plugin_name = plugin_name
        self.repo_root = repo_root
        self.config = config
        self._registries = registries
        self.surfaces: list[str] = []

    # -- parsers ----------------------------------------------------------

    def register_parser(self, *extensions: str):
        """Class decorator, identical contract to
        ``codegraph.parsers.register_parser``: subclass ``BaseParser``,
        return a ``FileIndex`` from ``parse()``. The parser output lands
        in the graph, the FTS, and the federated fan-out with no
        special-casing.
        """
        from codegraph.parsers import register_parser as _register

        self._mark("parsers")
        return _register(*extensions)

    # -- scanners ---------------------------------------------------------

    def register_scanner(self, scanner: FileScanner) -> None:
        """Register a post-parse scanner (see ``FileScanner``)."""
        self._mark("scanners")
        self._registries.scanners.append((self.plugin_name, scanner))

    # -- MCP tools --------------------------------------------------------

    def register_mcp_tools(self, fn: Callable[[Any], None]) -> None:
        """``fn(mcp)`` is called with the FastMCP instance at owner
        startup, exactly like the internal ``tools_*.py`` register
        functions. Ignored outside the owner process."""
        self._mark("mcp")
        self._registries.mcp_registrars.append((self.plugin_name, fn))

    # -- CLI --------------------------------------------------------------

    def register_cli(self, fn: Callable[[Any], None]) -> None:
        """``fn(subparsers)`` may add subcommands during CLI dispatch.
        Use ``parser.set_defaults(func=handler)`` on each added command;
        cgh's dispatcher falls back to ``args.func`` for plugin verbs."""
        self._mark("cli")
        self._registries.cli_registrars.append((self.plugin_name, fn))

    # -- generic extensions ----------------------------------------------

    def register_extension(self, namespace: str, obj: object) -> None:
        """Publish ``obj`` under a dotted namespace (e.g.
        ``summarize.backend``, ``integration``). Namespaces are plain
        strings, documented by whoever consumes them; this is how a
        plugin extends another plugin without core involvement."""
        self._mark("extensions")
        self._registries.extensions.setdefault(namespace, []).append(
            (self.plugin_name, obj)
        )

    def get_extensions(self, namespace: str) -> list[object]:
        """Read side of the registry, also available to core consumers
        via ``codegraph.plugins.get_extensions``."""
        return [obj for _, obj in self._registries.extensions.get(namespace, [])]

    # -- internal ---------------------------------------------------------

    def _mark(self, surface: str) -> None:
        if surface not in self.surfaces:
            self.surfaces.append(surface)


@dataclass
class _Registries:
    """Process-wide registries filled by PluginAPI calls. Owned by
    ``codegraph.plugins``; defined here to avoid an import cycle."""

    scanners: list[tuple[str, FileScanner]] = field(default_factory=list)
    mcp_registrars: list[tuple[str, Callable]] = field(default_factory=list)
    cli_registrars: list[tuple[str, Callable]] = field(default_factory=list)
    extensions: dict[str, list[tuple[str, object]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stable re-exports (lazy, PEP 562)
# ---------------------------------------------------------------------------
# The finding store is the real core<->plugin integration point, and the
# audit showed plugins reaching into codegraph.state/* for it, which made
# API_VERSION a false promise. These names are the supported surface;
# they resolve lazily so importing plugin_api stays light (no parser
# grammars, no sqlite) until a helper is actually used.

_REEXPORTS: dict[str, tuple[str, str]] = {
    # finding store
    "record_findings": ("codegraph.state.findings", "record_findings"),
    "query_findings": ("codegraph.state.findings", "query_findings"),
    "query_findings_ro": ("codegraph.state.findings", "query_findings_ro"),
    "findings_for_file": ("codegraph.state.findings", "findings_for_file"),
    "findings_db_path": ("codegraph.state.findings", "findings_db_path"),
    # audit trail + knowledge
    "activity_log": ("codegraph.state.activity", "log"),
    "knowledge_record": ("codegraph.state.call_log", "knowledge_record"),
    # config + repo resolution
    "load_config": ("codegraph.core.config", "load_config"),
    "find_codegraph_root": ("codegraph.core.config", "find_codegraph_root"),
    # parser lookup (triggers grammar loading on first use, by design)
    "is_supported": ("codegraph.parsers", "is_supported"),
    "get_parser_for_path": ("codegraph.parsers", "get_parser_for_path"),
    # misc supported helpers
    "git_hash_object": ("codegraph.state.scan_meta", "git_hash_object"),
    "quiet_subprocess_kwargs": ("codegraph.core.utils", "quiet_subprocess_kwargs"),
    "is_loopback_url": ("codegraph.core.utils", "is_loopback_url"),
    "resolve_children": ("codegraph.analysis.federation", "resolve_children"),
    "sync_static_rules": ("codegraph.state.guard", "sync_static_rules"),
    "loaded_plugins": ("codegraph.plugins", "loaded_plugins"),
    # parser building blocks (BaseParser subclassing per the docs)
    "BaseParser": ("codegraph.parsers.base", "BaseParser"),
    "FileIndex": ("codegraph.parsers.base", "FileIndex"),
    "SectionDef": ("codegraph.parsers.base", "SectionDef"),
}


def server_root() -> Path | None:
    """The owner process's repo root, read at call time (the module
    global is None at import and set in owner_main; capturing it at
    register time is the classic footgun). MCP tools registered by
    plugins call this instead of reaching into codegraph.server."""
    import codegraph.server as _srv

    return _srv._root


def __getattr__(name: str):
    target = _REEXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_REEXPORTS))
