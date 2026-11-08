# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Which MCP tools tools/list shows, per the [tools] table of
#              config.toml. CORE_TOOLS is the core set and compute_exposure
#              decides the listed names. Pure and free of fastmcp so the CLI
#              can read it; the owner applies it in
#              codegraph/server/tool_exposure.py. Plugin tools are always
#              listed, and an unknown setting lists every tool.

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# The core set: the tools the proxy study actually saw called, so a client
# with tool search keeps these loaded upfront (they carry anthropic/alwaysLoad)
# and a non-tool-search client under [tools] expose = "core" still has every
# tool it really uses; the long tail of never-called tools defers or hides.
# Of cgh's registered tools the study saw 24 ever called in 100k+ requests;
# this is the agent-facing part of that set, plus semantic_search (new, so no
# history yet); the three never agent-called index-mutating tools are left out.
# Grouped:
# search/navigation (pattern_search, symbol_lookup, search_symbols,
# semantic_search, fts_search, file_summary, find_callers, find_callees,
# endpoints, domain_map), docs (search_docs, doc_outline), task kickoff
# (context_for_task), knowledge (knowledge_record, knowledge_search,
# knowledge_list), session continuity (checkpoint, compact_session, resume;
# the SessionStart hook tells agents to call them after /clear and compaction),
# and freshness (scan_status, file_at_ref, tests_for). The index-mutating
# tools (incremental_reindex, index_changed_files, indexed_files) are hook- and
# status-driven, not agent-facing, so they stay deferred. Revisit as usage
# data grows.
CORE_TOOLS: frozenset[str] = frozenset(
    {
        "pattern_search",
        "symbol_lookup",
        "search_symbols",
        "semantic_search",
        "fts_search",
        "file_summary",
        "find_callers",
        "find_callees",
        "endpoints",
        "domain_map",
        "search_docs",
        "doc_outline",
        "context_for_task",
        "knowledge_record",
        "knowledge_search",
        "knowledge_list",
        "checkpoint",
        "compact_session",
        "resume",
        "scan_status",
        "file_at_ref",
        "tests_for",
    }
)


@dataclass(frozen=True, slots=True)
class ToolExposure:
    """The outcome of applying [tools] to the registered tools.

    ``listed`` is the set tools/list shows. ``core``, ``extra`` and
    ``plugin`` count the tools listed for each reason (a name is counted
    once, first match wins in that order). ``unknown_extra`` names the
    entries of ``extra`` that match no registered tool.
    """

    expose: str
    total: int
    listed: frozenset[str]
    core: int
    extra: int
    plugin: int
    unknown_extra: tuple[str, ...] = ()
    configured: str = ""

    @property
    def hidden(self) -> int:
        return self.total - len(self.listed)

    def label(self) -> str:
        """``all (64 listed)`` or ``core (15 + 2 extra + 2 plugin)``, with an
        ``auto -> `` prefix when the configured mode resolved per client."""
        if self.expose != "core":
            base = f"all ({len(self.listed)} listed)"
        else:
            base = f"core ({self.core} + {self.extra} extra + {self.plugin} plugin)"
        if self.configured and self.configured != self.expose:
            return f"{self.configured} -> {base}"
        return base

    def as_dict(self) -> dict[str, Any]:
        return {
            "expose": self.expose,
            "configured": self.configured or self.expose,
            "label": self.label(),
            "total": self.total,
            "listed": len(self.listed),
            "hidden": self.hidden,
            "core": self.core,
            "extra": self.extra,
            "plugin": self.plugin,
            "unknown_extra": list(self.unknown_extra),
        }


def resolve_expose(expose: str, client_name: str | None = None) -> str:
    """Turn the configured mode into the one applied to this client.

    ``auto`` (the default) lists every tool for a client that carries tool
    search, which keeps the core set loaded via anthropic/alwaysLoad and
    defers the rest; for any other client it lists only the core set, since
    those clients ignore alwaysLoad and would otherwise see the whole tail.
    Claude Code is the tool-search client today, matched on its reported
    client name. ``all`` and ``core`` pass through unchanged.
    """
    if expose != "auto":
        return expose
    if client_name and "claude" in client_name.lower():
        return "all"
    return "core"


def compute_exposure(
    all_names: Iterable[str],
    builtin_names: Iterable[str] | None,
    expose: str,
    extra: Sequence[str] = (),
    client_name: str | None = None,
) -> ToolExposure:
    """Decide which tools tools/list shows.

    ``builtin_names`` are the tools cgh itself registered; any other name
    is a plugin tool and is always listed. When it is None (cgh never
    recorded its own tools) nothing can be told apart, so everything is
    listed. ``expose`` may be ``auto``, resolved per ``client_name`` (see
    ``resolve_expose``); only a resolved ``core`` narrows the list.
    """
    configured = expose
    expose = resolve_expose(expose, client_name)
    names = frozenset(all_names)
    unknown = tuple(n for n in extra if n not in names)
    if expose != "core" or builtin_names is None:
        return ToolExposure(
            expose="all",
            total=len(names),
            listed=names,
            core=0,
            extra=0,
            plugin=0,
            unknown_extra=unknown if expose == "core" else (),
            configured=configured,
        )
    builtin = frozenset(builtin_names)
    plugin = names - builtin
    core = (names & CORE_TOOLS) - plugin
    extra_listed = frozenset(n for n in extra if n in names) - core - plugin
    return ToolExposure(
        expose="core",
        total=len(names),
        listed=core | extra_listed | plugin,
        core=len(core),
        extra=len(extra_listed),
        plugin=len(plugin),
        unknown_extra=unknown,
        configured=configured,
    )
