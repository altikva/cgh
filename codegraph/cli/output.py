# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The generic --out option for every cgh verb that emits a
#              reusable artifact (markdown, JSON, Mermaid). One flag,
#              one behavior everywhere: the result still prints on
#              stdout, --out also writes it to a file, and without it
#              an interactive session gets a one-line stderr tip.
#              Plugin CLIs reach these two helpers via plugin_api.

from __future__ import annotations

import sys
from pathlib import Path


def add_out_option(parser, what: str = "the result") -> None:
    """Attach the shared --out PATH argument to a subcommand parser."""
    parser.add_argument(
        "--out",
        metavar="PATH",
        default="",
        help=f"Also write {what} to this file",
    )


def emit_result(text: str, out: str = "", hint: str = "result.md") -> None:
    """Print an artifact on stdout, honoring the shared --out contract.

    With ``out``: the artifact is also written there (parent dirs
    created) and the save is confirmed on stderr. Without it, and only
    when stderr is a TTY (never in pipes or CI), a one-line tip
    advertises the flag with ``hint`` as the example filename."""
    print(text)
    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        print(f"saved to {target}", file=sys.stderr)
    elif sys.stderr.isatty():
        print(f"tip: add --out {hint} to save this output", file=sys.stderr)
