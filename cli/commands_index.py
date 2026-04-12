# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands — index, watch, serve, force-index.

from __future__ import annotations

import os
import sys
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from codegraph.cli import LOGO, _lang_color, _short_path, console

# ---------------------------------------------------------------------------
# cmd_index
# ---------------------------------------------------------------------------


def cmd_index(args) -> None:
    from codegraph.indexer import index_repo

    root = os.path.abspath(args.root)

    console.print(LOGO)
    console.print(f"[dim]Repository:[/dim] [bold]{root}[/bold]\n")

    task_id = None

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[status]}[/dim]"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:

        def on_discovery(total, method):
            nonlocal task_id
            label = "git ls-files" if method == "git_ls_files" else "os.walk"
            desc = f"Indexing ({label})"
            if total > 0:
                task_id = progress.add_task(desc, total=total, status="")
            else:
                task_id = progress.add_task(desc, total=None, status="scanning...")

        def on_file(file_path, status, stats):
            if task_id is not None:
                short = _short_path(str(file_path), root)
                suffix = file_path.suffix.lower()
                color = _lang_color(suffix)
                progress.update(
                    task_id,
                    advance=1,
                    status=f"[{color}]{short}[/{color}]",
                )

        stats = index_repo(root, on_file=on_file, on_discovery=on_discovery)

    # Summary
    console.print()
    table = Table(box=box.ROUNDED, title="Index Summary", title_style="bold")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Files indexed", f"[green]{stats['indexed']}[/green]")
    table.add_row("Files skipped", f"[dim]{stats['skipped']}[/dim]")
    table.add_row("Errors", f"[red]{stats['errors']}[/red]" if stats["errors"] > 0 else "[dim]0[/dim]")
    table.add_row("Elapsed", f"[cyan]{stats['elapsed_s']}s[/cyan]")
    table.add_row("Method", f"[dim]{stats.get('method', '?')}[/dim]")
    console.print(table)


# ---------------------------------------------------------------------------
# cmd_watch
# ---------------------------------------------------------------------------


def cmd_watch(args) -> None:
    from codegraph.indexer import index_repo
    from codegraph.watcher import watch_forever

    root = os.path.abspath(args.root)

    console.print(LOGO)
    console.print(f"[dim]Watching:[/dim] [bold]{root}[/bold]\n")

    with console.status("[bold blue]Initial index...", spinner="dots"):
        stats = index_repo(root, verbose=False)

    console.print(f"[green]Initial index done[/green] -- {stats['indexed']} files in {stats['elapsed_s']}s")
    console.print("[dim]Watching for changes... (Ctrl-C to stop)[/dim]\n")
    watch_forever(root)


# ---------------------------------------------------------------------------
# cmd_serve
# ---------------------------------------------------------------------------


def cmd_serve(args) -> None:
    root = os.path.abspath(args.root)
    new_argv = ["codegraph.server", "--root", root]
    if args.watch:
        new_argv.append("--watch")
    if args.reindex:
        new_argv.append("--reindex")
    sys.argv = new_argv
    from codegraph.server import main

    main()


# ---------------------------------------------------------------------------
# cmd_force_index
# ---------------------------------------------------------------------------


def cmd_force_index(args) -> None:
    from codegraph.indexer import _PARSERS, index_file

    root = Path(os.path.abspath(args.root))
    targets = args.paths

    if not args.yes:
        console.print(
            Panel(
                "\n".join(f"  [bold]{t}[/bold]" for t in targets),
                title="[yellow]Force Index[/yellow]",
                subtitle="[dim]Bypasses .gitignore and .git/info/exclude[/dim]",
                border_style="yellow",
            )
        )
        if console.input("[yellow]Continue? [y/N][/yellow] ").strip().lower() not in ("y", "yes"):
            console.print("[dim]Aborted.[/dim]")
            return

    indexed = 0
    with console.status("[bold yellow]Force indexing...", spinner="dots") as status:
        for p in targets:
            target = Path(p) if os.path.isabs(p) else root / p
            if target.is_file():
                ok = index_file(target, root, force=True)
                if ok:
                    indexed += 1
                    status.update(f"[bold yellow]Indexed:[/bold yellow] {target.relative_to(root)}")
            elif target.is_dir():
                for dirpath, _, filenames in os.walk(target):
                    for filename in filenames:
                        full = Path(dirpath) / filename
                        if full.suffix.lower() in _PARSERS:
                            ok = index_file(full, root, force=True)
                            if ok:
                                indexed += 1
                                status.update(f"[bold yellow]Indexed:[/bold yellow] {full.relative_to(root)}")
            else:
                console.print(f"  [red]x[/red] {p} (not found)")

    console.print(f"[green]Force-indexed {indexed} file(s)[/green]")
