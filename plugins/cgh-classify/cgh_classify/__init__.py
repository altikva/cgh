# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the `cgh classify` CLI
#              verbs (label, train, review, status), and the inline
#              classify scanner only with [plugin.classify]
#              scan_on_index = true. Frozen since 0.2.0, see the README.

from __future__ import annotations

CGH_PLUGIN_API = 1


def _scan_on_index(config: dict) -> bool:
    value = config.get("scan_on_index", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config))
    # The confidentiality labels fed the egress gate and the guard, both
    # gone with secure mode in cgh 0.15.0; nothing reads them at index
    # time any more, so the per-file scan is opt-in.
    if _scan_on_index(api.config):
        from .scanner import ClassifyScanner

        api.register_scanner(ClassifyScanner(api.config, api.repo_root))
