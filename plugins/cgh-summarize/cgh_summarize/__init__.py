# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the deferred summarize
#              scanner, the `cgh summarize` and `cgh insights` CLI verbs,
#              and the summaries / corpus_insights MCP tools. Third-party
#              summarizer backends are consumed from the summarize.backend
#              extension namespace.

from __future__ import annotations

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .scanner import SummarizeScanner

    def extras():
        return api.get_extensions("summarize.backend")

    api.register_scanner(SummarizeScanner(api.config, api.repo_root, extras_fn=extras))

    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config, extras))

    from .mcp_tools import make_mcp_registrar

    api.register_mcp_tools(make_mcp_registrar(api.config, extras))
