# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Applies [tools] expose in the owner. ToolExposureMiddleware
#              narrows the tools/list response only; tools/call never goes
#              through it, so a hidden tool still answers when called by
#              name (hooks, scripts). registered_tool_names lets the owner
#              record cgh's own tools before plugins add theirs. The
#              decision itself lives in cgh/core/tool_exposure.py.
#              Every failure lists all tools.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware

from cgh.core.tool_exposure import ToolExposure, compute_exposure

_log = logging.getLogger(__name__)


def registered_tool_names(server: Any) -> frozenset[str]:
    """Every tool registered on a FastMCP server, before any middleware.

    Synchronous (runs its own event loop), so call it outside a running
    loop: the owner does, before it starts serving.
    """
    if hasattr(server, "list_tools"):
        try:
            tools = asyncio.run(server.list_tools(run_middleware=False))
        except TypeError:  # a fastmcp without the run_middleware switch
            tools = asyncio.run(server.list_tools())
        return frozenset(t.name for t in tools)
    return frozenset(asyncio.run(server.get_tools()))  # fastmcp 2.x


def _owner_root() -> Path | None:
    # Read at call time: the owner sets cgh.server._root after import.
    import cgh.server as _srv

    return _srv._root


def _client_name(context: Any) -> str | None:
    """The MCP client's reported name (``clientInfo.name`` from initialize),
    or None when it can't be read. Defensive across fastmcp versions: the
    exposure must never fail because the client info is shaped unexpectedly."""
    try:
        session = context.fastmcp_context.session
        params = getattr(session, "client_params", None)
        info = getattr(params, "client_info", None)
        name = getattr(info, "name", None)
        return name if isinstance(name, str) else None
    except Exception:
        return None


class ToolExposureMiddleware(Middleware):
    """Narrows the tools/list response per [tools]; leaves calls alone.

    ``builtin_names`` is filled by the owner with the tools cgh registered
    itself, before plugins register theirs. Until then, and on any error,
    the listing passes through untouched.
    """

    def __init__(self, root: Callable[[], Path | None] = _owner_root) -> None:
        super().__init__()
        self._root = root
        self.builtin_names: frozenset[str] | None = None
        self.registered_names: frozenset[str] | None = None
        self._warned_unknown: set[str] = set()

    def record(self, builtin: frozenset[str], registered: frozenset[str]) -> None:
        """Remember cgh's own tools and the full set after plugins loaded."""
        self.builtin_names = builtin
        self.registered_names = registered

    def exposure(
        self,
        all_names: Iterable[str] | None = None,
        client_name: str | None = None,
    ) -> ToolExposure:
        """The exposure for the current config and client. Raises on a config
        error; callers that must not fail use it inside a try."""
        from cgh.core.config import load_config

        names = list(
            all_names if all_names is not None else (self.registered_names or ())
        )
        root = self._root()
        if root is None:
            return compute_exposure(names, None, "all")
        cfg = load_config(root)
        result = compute_exposure(
            names,
            self.builtin_names,
            cfg.tools_expose,
            cfg.tools_extra,
            client_name=client_name,
        )
        fresh = [n for n in result.unknown_extra if n not in self._warned_unknown]
        if fresh:
            self._warned_unknown.update(fresh)
            _log.warning(
                "[tools] extra names no registered tool, ignored: %s",
                ", ".join(fresh),
            )
        return result

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        try:
            exposure = self.exposure(
                [t.name for t in tools], client_name=_client_name(context)
            )
        except Exception as exc:
            _log.warning("[tools] exposure failed, listing every tool: %s", exc)
            return tools
        if exposure.expose != "core":
            return tools
        return [t for t in tools if t.name in exposure.listed]
