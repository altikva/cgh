# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands — `cgh federate` verbs (add/remove/list/verify).
#              Manages the parent project's federated subrepos, declared in
#              .codegraph/config.toml under [codegraph] subrepos = […].

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console
from rich.table import Table

from codegraph.federation import (
    add_subrepo,
    remove_subrepo,
    resolve_children,
    verify_child,
)

console = Console()


def cmd_federate(args) -> None:
    """Dispatcher for `cgh federate <verb>`."""
    action = getattr(args, "action", None) or "list"
    if action == "add":
        return _cmd_add(args)
    if action == "remove":
        return _cmd_remove(args)
    if action == "verify":
        return _cmd_verify(args)
    if action == "list":
        return _cmd_list(args)
    console.print(f"[red]Unknown action: {action}[/red]")
    console.print("[dim]Usage: cgh federate add <path> | remove <path> | list | verify[/dim]")


def _cmd_add(args) -> None:
    paths = getattr(args, "paths", None) or []
    if not paths:
        console.print("[red]Usage: cgh federate add <path> [<path> …][/red]")
        return
    root = Path(os.path.abspath(args.root))
    for raw in paths:
        try:
            child, status = add_subrepo(root, raw)
        except ValueError as exc:
            console.print(f"[red]✗ {raw}:[/red] {exc}")
            continue

        if not status.initialized:
            console.print(
                f"[yellow]⚠ {child}:[/yellow] added to config but no .codegraph/ found.\n"
                f"  Run [cyan]cgh init[/cyan] inside the subrepo first."
            )
        elif not status.has_kuzu:
            console.print(
                f"[yellow]⚠ {child}:[/yellow] .codegraph/ exists but graph.db missing.\n"
                f"  Run [cyan]cgh index[/cyan] inside the subrepo."
            )
        else:
            console.print(f"[green]✓ {child}[/green] federated.")


def _cmd_remove(args) -> None:
    paths = getattr(args, "paths", None) or []
    if not paths:
        console.print("[red]Usage: cgh federate remove <path>[/red]")
        return
    root = Path(os.path.abspath(args.root))
    for raw in paths:
        if remove_subrepo(root, raw):
            console.print(f"[green]✓ removed[/green] {raw}")
        else:
            console.print(f"[dim]not federated: {raw}[/dim]")


def _cmd_list(args) -> None:
    root = Path(os.path.abspath(args.root))
    children = resolve_children(root)
    if not children:
        console.print("[dim]No subrepos federated.[/dim]")
        console.print("[dim]Add one with:[/dim] [cyan]cgh federate add <path>[/cyan]")
        return
    _render_status_table(root, children)


def _cmd_verify(args) -> None:
    """Same as list, plus exits non-zero if any child is broken."""
    root = Path(os.path.abspath(args.root))
    children = resolve_children(root)
    if not children:
        console.print("[dim]No subrepos to verify.[/dim]")
        return
    statuses = [verify_child(c) for c in children]
    _render_status_table(root, children, statuses=statuses)
    bad = [s for s in statuses if not s.ok]
    if bad:
        console.print(f"\n[red]{len(bad)} subrepo(s) need attention.[/red]")
        raise SystemExit(1)


def _render_status_table(
    root: Path,
    children: list[Path],
    statuses: list | None = None,
) -> None:
    if statuses is None:
        statuses = [verify_child(c) for c in children]

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("subrepo")
    table.add_column("status")
    table.add_column("git")
    table.add_column("path")

    for child, status in zip(children, statuses):
        try:
            display = "./" + str(child.relative_to(root))
        except ValueError:
            display = str(child)

        if not status.exists:
            badge = "[red]missing[/red]"
        elif not status.initialized:
            badge = "[yellow]not initialized[/yellow]"
        elif not status.has_kuzu:
            badge = "[yellow]no graph.db[/yellow]"
        else:
            badge = "[green]ok[/green]"

        git = "[dim]yes[/dim]" if status.is_git_repo else "[dim]no[/dim]"
        table.add_row(child.name, badge, git, display)
    console.print(table)
