# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point. Registers the `cgh proxy` CLI
#              (serve, list, show): an experimental local proxy between
#              Claude Code and a LiteLLM gateway.

from __future__ import annotations

CGH_PLUGIN_API = 1


class ProxyError(Exception):
    """Base error of the cgh-proxy plugin."""


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar())
