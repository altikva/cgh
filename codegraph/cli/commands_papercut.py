# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh papercut` verb: log and search papercuts, the tooling and
#              environment time-sinks that slow a session down, in this repo's
#              knowledge store. A papercut is stored as a knowledge entry with
#              kind "gotcha" and the "papercut" tag, so it is searchable with
#              knowledge_search and surfaces in the resume bundle. Read them
#              first when tooling fails; add one when you lose time and find the
#              fix. `cgh papercut` lists, `cgh papercut <query>` searches, and
#              `cgh papercut add "<symptom>" --fix "<fix>"` records.

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from codegraph.cli import console

_TAG = "papercut"


def register_papercut_parser(sub) -> None:
    p = sub.add_parser(
        "papercut",
        help="Log or search papercuts (tooling time-sinks) in this repo's knowledge",
    )
    p.add_argument(
        "words",
        nargs="*",
        default=[],
        help="nothing = list recent; a query = search; 'add <symptom>' = record",
    )
    p.add_argument(
        "--fix",
        default="",
        help="For add: the fix that worked (exact command or change)",
    )
    p.add_argument(
        "--project",
        default="",
        help="For add: scope label (default: this repo's folder name)",
    )
    p.add_argument("--limit", type=int, default=15, help="Max results to show")
    p.add_argument("--root", default=os.getcwd(), help="Repo root (default: cwd)")


def _fix_line(body: str) -> str:
    """Pull the Fix line out of a stored papercut body for the compact list."""
    for line in body.splitlines():
        s = line.strip()
        if s.lower().startswith("fix:"):
            return s[4:].strip()
    return body.strip().splitlines()[0] if body.strip() else ""


def cmd_papercut(args: argparse.Namespace) -> None:
    from codegraph.state.call_log import knowledge_record, knowledge_search

    root = Path(os.path.abspath(args.root))
    words = list(args.words or [])

    # --- add -------------------------------------------------------------
    if words and words[0] == "add":
        symptom = " ".join(words[1:]).strip()
        if not symptom:
            console.print(
                '[red]Usage:[/red] cgh papercut add "<symptom>" --fix "<fix>" [--project P]'
            )
            raise SystemExit(1)
        project = args.project.strip() or root.name
        fix = args.fix.strip()
        body_lines = [f"Symptom: {symptom}"]
        if fix:
            body_lines.append(f"Fix: {fix}")
        body_lines.append(f"Project: {project}")
        title = symptom if len(symptom) <= 70 else symptom[:67] + "..."
        row_id = knowledge_record(
            title=title,
            body="\n".join(body_lines),
            kind="gotcha",
            tags=_TAG,
            repo_root=root,
        )
        console.print(
            f"[green]Papercut logged[/green] (knowledge #{row_id}, tag [cyan]{_TAG}[/cyan])."
        )
        if not fix:
            console.print(
                "[dim]Tip: pass --fix so the next session gets the answer, not just the symptom.[/dim]"
            )
        return

    # --- list / search ---------------------------------------------------
    query = " ".join(words).strip()
    # An empty query lists the papercuts; a query narrows within them. The tag
    # lives in the FTS-indexed tags column, so matching it returns papercuts.
    search_term = f"{_TAG} {query}".strip() if query else _TAG
    rows = knowledge_search(
        search_term, kind="gotcha", limit=max(args.limit * 3, 30), repo_root=root
    )
    hits = [r for r in rows if _TAG in (r.get("tags") or "")][: args.limit]

    if not hits:
        if query:
            console.print(f"[yellow]No papercut matches[/yellow] for '{query}'.")
        else:
            console.print(
                "[dim]No papercuts logged yet. Add one with:[/dim] "
                'cgh papercut add "<symptom>" --fix "<fix>"'
            )
        return

    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("date", style="dim", no_wrap=True)
    table.add_column("symptom")
    table.add_column("fix", overflow="fold")
    for r in hits:
        try:
            date = datetime.fromtimestamp(float(r.get("ts") or 0)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            date = "?"
        table.add_row(
            date, (r.get("title") or "").strip(), _fix_line(r.get("body") or "")
        )
    header = f"papercuts matching '{query}'" if query else "papercuts (newest first)"
    console.print(f"[bold]{header}[/bold]  [dim]({len(hits)})[/dim]")
    console.print(table)
    console.print(
        "[dim]Read these first when tooling fails. Log a new one with cgh papercut add.[/dim]"
    )
