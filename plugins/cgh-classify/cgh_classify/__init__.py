# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the inline classify
#              scanner and the `cgh classify` CLI verbs (label, train,
#              review, status).

from __future__ import annotations

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .cli import make_cli_registrar
    from .scanner import ClassifyScanner

    api.register_scanner(ClassifyScanner(api.config, api.repo_root))
    api.register_cli(make_cli_registrar(api.config))
