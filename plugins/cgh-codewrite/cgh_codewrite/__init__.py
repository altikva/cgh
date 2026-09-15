# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point. Registers the `cgh codewrite` CLI
#              verbs (pick, gen) and the codewrite_pick / code_write MCP
#              tools: pick the file to mirror, then generate the target from
#              a spec with a cheap model, behind the egress gate.

from __future__ import annotations

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config))

    from .mcp_tools import make_mcp_registrar

    api.register_mcp_tools(make_mcp_registrar(api.config))
