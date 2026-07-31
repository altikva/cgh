# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: chains a crash-capturing
#              sys.excepthook (allowlist payload spooled locally, one
#              stderr line, then the previous hook runs) and registers
#              the `cgh bug` CLI verbs. Installing the plugin consents
#              to CAPTURE only; nothing is ever sent without an explicit
#              `cgh bug send`.

from __future__ import annotations

import os
import sys

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config))
    _install_excepthook()


_installed = False


def _install_excepthook() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    previous = sys.excepthook

    def hook(exc_type, exc_value, tb):
        _capture(exc_type, exc_value, tb)
        previous(exc_type, exc_value, tb)

    sys.excepthook = hook


def _capture(exc_type, exc_value, tb) -> None:
    """Best effort, never a second crash."""
    try:
        if exc_type is KeyboardInterrupt:
            return
        from codegraph.plugin_api import find_codegraph_root

        root = find_codegraph_root(os.getcwd())
        if root is None:
            return
        from .payload import build_report
        from .spool import write_report

        command = sys.argv[1] if len(sys.argv) > 1 else ""
        payload = build_report(exc_type, exc_value, tb, command=command)
        write_report(root, payload)
        print(
            f"cgh: crash report {payload['report_id']} spooled locally "
            "(cgh bug preview last to inspect, cgh bug send to submit).",
            file=sys.stderr,
        )
    except Exception:
        pass
