# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The landing screen is the first thing a user sees, and the
#              README ships a capture of it. Its Examples panel used to be
#              aligned by hand with literal spaces, and four of the nine
#              rows were off, so the column is asserted here.

from __future__ import annotations

from rich.console import Console

PURPOSES = [
    "Setup in any project",
    "Find symbols",
    "Call graph (tree)",
    "Doc structure (tree)",
    "Full statistics",
    "Call graph in browser",
    "Multi-repo graph",
    "Health check",
    "MCP server",
]


def _landing_screen() -> list[str]:
    """Render the landing screen through a recording console.

    Both namespaces are swapped: modules bind `console` at import time, so
    reassigning only one of them would record nothing.
    """
    import codegraph.__main__ as main_module
    import codegraph.cli as cli

    recorder = Console(record=True, width=110, no_color=True)
    previous = (cli.console, main_module.console)
    cli.console, main_module.console = recorder, recorder
    try:
        main_module._print_help()
    finally:
        cli.console, main_module.console = previous
    return recorder.export_text().splitlines()


class TestExamplesPanel:
    def test_every_description_starts_in_the_same_column(self):
        lines = _landing_screen()
        start = next(i for i, ln in enumerate(lines) if "Examples" in ln)
        panel = lines[start:]

        columns = {}
        for purpose in PURPOSES:
            line = next((ln for ln in panel if purpose in ln), None)
            assert line is not None, f"{purpose!r} missing from the Examples panel"
            columns[purpose] = line.index(purpose)

        assert len(set(columns.values())) == 1, f"ragged column: {columns}"

    def test_the_panel_lists_every_example(self):
        panel = "\n".join(_landing_screen())
        for purpose in PURPOSES:
            assert purpose in panel
