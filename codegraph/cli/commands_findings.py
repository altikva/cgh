# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh findings`: query the finding store from the shell,
#              filters by file, key prefix and severity, federated over
#              subrepos with a scope column.

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rich import box
from rich.table import Table

from codegraph.cli import _short_path, console


def cmd_findings(args: argparse.Namespace) -> None:
    from codegraph.analysis.federation import has_subrepos, resolve_children
    from codegraph.state.findings import (
        findings_db_path,
        query_findings,
        query_findings_ro,
    )

    root = os.path.abspath(args.root)
    file_path = getattr(args, "file", "") or ""
    if file_path and not os.path.isabs(file_path):
        file_path = str(Path(root) / file_path)
    key_prefix = getattr(args, "key", "") or ""
    severity = getattr(args, "severity", "") or ""
    limit = getattr(args, "limit", 100)

    rows: list[dict] = []
    for row in query_findings(
        root, key_prefix=key_prefix, severity=severity, file_path=file_path, limit=limit
    ):
        row["scope"] = "parent"
        rows.append(row)
    federated = has_subrepos(root)
    for child in resolve_children(root) if federated else []:
        for row in query_findings_ro(
            findings_db_path(child), key_prefix=key_prefix, limit=limit
        ):
            if severity and row.get("severity") != severity:
                continue
            if file_path and row.get("file") != file_path:
                continue
            row["scope"] = child.name
            rows.append(row)

    if args.json:
        print(json.dumps({"total": len(rows), "findings": rows}, indent=2))
        return

    if not rows:
        console.print("[dim]No findings recorded.[/dim]")
        console.print(
            "[dim]Findings come from scanner plugins; see[/dim] "
            "[cyan]cgh plugins[/cyan][dim] for what's installed.[/dim]"
        )
        return

    sev_style = {"info": "dim", "warn": "yellow", "block": "red"}
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAD)
    table.add_column("key")
    table.add_column("value", overflow="fold", max_width=40)
    table.add_column("severity")
    table.add_column("location", style="dim")
    if federated:
        table.add_column("scope", style="dim")

    for row in rows:
        loc = _short_path(row["file"], root)
        if row.get("line"):
            loc += f":{row['line']}"
        style = sev_style.get(row["severity"], "")
        cells = [
            row["key"],
            row["value"],
            f"[{style}]{row['severity']}[/{style}]" if style else row["severity"],
            loc,
        ]
        if federated:
            cells.append(row.get("scope", "parent"))
        table.add_row(*cells)
    console.print(table)
    console.print(f"[dim]{len(rows)} finding(s).[/dim]")
