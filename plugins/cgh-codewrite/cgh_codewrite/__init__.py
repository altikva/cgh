# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point. Registers the `cgh codewrite` CLI
#              verb and the codewrite_pick MCP tool. This is the reference
#              selection surface: it finds the file to mirror. Code
#              generation (the model call behind the egress gate) lands on
#              top of this in a later change.

from __future__ import annotations

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config))

    from .mcp_tools import make_mcp_registrar

    api.register_mcp_tools(make_mcp_registrar(api.config))
