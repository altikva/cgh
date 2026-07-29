# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh plugins`: list discovered plugins with their status
#              (active / disabled / incompatible / broken), version,
#              declared API version, and the surfaces they registered.

from __future__ import annotations

import argparse
import json

from rich import box
from rich.table import Table

from codegraph.cli import console


def cmd_plugins(args: argparse.Namespace) -> None:
    from codegraph.plugins import load_plugins

    root = getattr(args, "root", None)
    records = load_plugins(root)

    if getattr(args, "json", False):
        print(
            json.dumps(
                [
                    {
                        "name": r.name,
                        "status": r.status,
                        "version": r.version or None,
                        "api_version": r.api_version,
                        "surfaces": r.surfaces,
                        "reason": r.reason or None,
                    }
                    for r in records
                ],
                indent=2,
            )
        )
        return

    if not records:
        console.print("[dim]No plugins installed.[/dim]")
        console.print(
            "[dim]Install one with pip; anything exposing the [cyan]cgh[/cyan] "
            "entry point group is discovered on the next run.[/dim]"
        )
        return

    badges = {
        "active": "[green]active[/green]",
        "disabled": "[dim]disabled[/dim]",
        "incompatible": "[yellow]incompatible[/yellow]",
        "broken": "[red]broken[/red]",
        "duplicate": "[yellow]duplicate[/yellow]",
    }
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAD)
    table.add_column("plugin")
    table.add_column("status")
    table.add_column("version")
    table.add_column("api")
    table.add_column("surfaces")
    table.add_column("note", overflow="fold")

    for r in records:
        table.add_row(
            r.name,
            badges.get(r.status, r.status),
            r.version or "[dim]?[/dim]",
            str(r.api_version) if r.api_version is not None else "[dim]-[/dim]",
            ", ".join(r.surfaces) if r.surfaces else "[dim]-[/dim]",
            r.reason or "",
        )
    console.print(table)
