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

# The tools an agent needs on almost every task, listed when
# [tools] expose = "core". Navigation (pattern_search, symbol_lookup,
# search_symbols, file_summary, find_callers, endpoints, tests_for,
# file_at_ref), task kickoff (context_for_task), the knowledge store
# (knowledge_record, knowledge_search), freshness (scan_status) and session
# continuity (checkpoint, compact_session, resume). compact_session and
# resume are here because the SessionStart hook tells agents to call them
# after /clear and after a compaction (cli/commands_session.py); dropping
# them would leave that instruction pointing at a tool the agent cannot see.
CORE_TOOLS: frozenset[str] = frozenset(
    {
        "pattern_search",
        "symbol_lookup",
        "search_symbols",
        "file_summary",
        "tests_for",
        "find_callers",
        "endpoints",
        "context_for_task",
        "knowledge_record",
        "knowledge_search",
        "checkpoint",
        "compact_session",
        "resume",
        "scan_status",
        "file_at_ref",
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

    @property
    def hidden(self) -> int:
        return self.total - len(self.listed)

    def label(self) -> str:
        """``all (64 listed)`` or ``core (15 + 2 extra + 2 plugin)``."""
        if self.expose != "core":
            return f"all ({len(self.listed)} listed)"
        return f"core ({self.core} + {self.extra} extra + {self.plugin} plugin)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "expose": self.expose,
            "label": self.label(),
            "total": self.total,
            "listed": len(self.listed),
            "hidden": self.hidden,
            "core": self.core,
            "extra": self.extra,
            "plugin": self.plugin,
            "unknown_extra": list(self.unknown_extra),
        }


def compute_exposure(
    all_names: Iterable[str],
    builtin_names: Iterable[str] | None,
    expose: str,
    extra: Sequence[str] = (),
) -> ToolExposure:
    """Decide which tools tools/list shows.

    ``builtin_names`` are the tools cgh itself registered; any other name
    is a plugin tool and is always listed. When it is None (cgh never
    recorded its own tools) nothing can be told apart, so everything is
    listed. Only ``expose == "core"`` narrows the list.
    """
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
    )
