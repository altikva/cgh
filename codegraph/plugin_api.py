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
#              changes here bump API_VERSION.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

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
    path by the owner, keyed by blob SHA (NER tier, model calls).

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
        registries: "_Registries",
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
