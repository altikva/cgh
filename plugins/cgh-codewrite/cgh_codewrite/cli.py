# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI verb `cgh codewrite pick`: report the reference file a
#              generator should mirror for a given target, and why. Code
#              generation is added as a sibling subcommand later.

from __future__ import annotations

import os
from pathlib import Path


def make_cli_registrar(config: dict):
    def add_cli(sub) -> None:
        p = sub.add_parser("codewrite", help="Pattern-matched code generation helpers")
        actions = p.add_subparsers(dest="cw_action")

        pick = actions.add_parser(
            "pick", help="Show the reference file to mirror for a target"
        )
        pick.add_argument("--target", required=True, help="File you intend to write")
        pick.add_argument(
            "--reference", default="", help="Force a reference instead of picking one"
        )
        pick.add_argument("--root", default=os.getcwd())
        pick.set_defaults(func=lambda args: _cmd_pick(args, config))

    return add_cli


def _cmd_pick(args, config: dict) -> None:
    from rich.console import Console

    from .picker import CodeWriteError, pick_reference

    console = Console()
    root = Path(os.path.abspath(args.root))
    try:
        result = pick_reference(root, args.target, args.reference or None)
    except CodeWriteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if result["reference"] is None:
        console.print(f"[yellow]{result['reason']}[/yellow]")
        if result["graph_available"] is False:
            console.print(
                "[dim]graph unavailable (no index, or an owner holds the "
                "lock); picked from the filesystem only.[/dim]"
            )
        return

    console.print(f"[bold]reference:[/bold] {result['reference']}")
    console.print(f"[dim]{result['reason']}[/dim]")
    others = [c for c in result["candidates"] if c != result["reference"]]
    if others:
        console.print("[dim]other candidates: " + ", ".join(others) + "[/dim]")
    if result["graph_available"] is False:
        console.print("[dim]graph unavailable; filesystem-only pick.[/dim]")
