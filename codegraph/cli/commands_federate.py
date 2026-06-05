# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands for the `cgh federate` verbs:
#              add / remove / list / verify  (config mutation + status)
#              up / down                     (lifecycle of each child's owner)
#              Manages the parent project's federated subrepos, declared in
#              .codegraph/config.toml under [codegraph] subrepos = […].

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console
from rich.table import Table

from codegraph.analysis.federation import (
    add_subrepo,
    child_owner_status,
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
    if action == "up":
        return _cmd_up(args)
    if action == "down":
        return _cmd_down(args)
    console.print(f"[red]Unknown action: {action}[/red]")
    console.print("[dim]Usage: cgh federate add <path> | remove <path> | list | verify | up | down[/dim]")


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
        elif not status.has_graphdb:
            console.print(
                f"[yellow]⚠ {child}:[/yellow] .codegraph/ exists but no graph DB "
                f"(graph.duckdb / graph.db) found.\n"
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


def _cmd_up(args) -> None:
    """Ensure each federated child has its own owner running with --watch.

    For children whose owner is already alive: no-op. For children that are
    initialized but ownerless, drop a keepalive marker in the child's
    `.codegraph/workers/` and spawn `cgh _serve_owner --watch` for them.
    The keepalive survives this CLI process, so the child's owner stays
    up across Claude sessions just like the parent owner does.
    """
    from codegraph.state.ipc import is_owner_alive, register_keepalive, spawn_owner

    root = Path(os.path.abspath(args.root))
    children = resolve_children(root)
    if not children:
        console.print("[dim]No subrepos federated. Nothing to start.[/dim]")
        return

    for child in children:
        st = verify_child(child)
        if not st.ok:
            console.print(
                f"[yellow]⚠ {child.name}[/yellow] skipped, "
                f"{'not initialized' if not st.initialized else 'no graph DB'}"
            )
            continue
        if is_owner_alive(child):
            owner = child_owner_status(child)
            console.print(f"[dim]• {child.name} already running (pid {owner.pid}, port {owner.port})[/dim]")
            continue
        register_keepalive(child)
        port = spawn_owner(child, watch=True, reindex=False)
        if port is None:
            console.print(f"[red]✗ {child.name}[/red] failed to start (see {child}/.codegraph/owner.log)")
        else:
            console.print(f"[green]✓ {child.name}[/green] started on port {port}")

    console.print(
        "\n[dim]Children stay alive via keepalive markers in each "
        ".codegraph/workers/. Stop them all with:[/dim] [cyan]cgh federate down[/cyan]"
    )


def _cmd_down(args) -> None:
    """Stop the owner of each federated child (if running) and remove its
    keepalive marker. Doesn't touch children whose owners were started by
    something other than `cgh federate up`, they'll just lose their
    keepalive and exit on their own when their last worker disconnects.
    """
    import os as _os

    from codegraph.state.ipc import (
        is_pid_alive,
        owner_pidfile,
        port_file,
        unregister_keepalive,
    )

    root = Path(_os.path.abspath(args.root))
    children = resolve_children(root)
    if not children:
        console.print("[dim]No subrepos federated.[/dim]")
        return

    for child in children:
        unregister_keepalive(child)
        pf = owner_pidfile(child)
        if not pf.exists():
            console.print(f"[dim]• {child.name} already stopped[/dim]")
            continue
        try:
            pid = int(pf.read_text().strip())
            from codegraph.state.pidfile import terminate

            terminate(pid, graceful_timeout=5.0)
            if is_pid_alive(pid):
                console.print(f"[yellow]⚠ {child.name}[/yellow] force-killed pid {pid}")
            else:
                console.print(f"[green]✓ {child.name}[/green] stopped pid {pid}")
            pf.unlink(missing_ok=True)
            port_file(child).unlink(missing_ok=True)
        except (ValueError, ProcessLookupError, PermissionError):
            console.print(f"[dim]• {child.name} already gone[/dim]")
            pf.unlink(missing_ok=True)
            port_file(child).unlink(missing_ok=True)


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
    table.add_column("owner")
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
        elif not status.has_graphdb:
            badge = "[yellow]no graph DB[/yellow]"
        else:
            backend = "duckdb" if status.has_duckdb else "kuzu"
            badge = f"[green]ok[/green] [dim]({backend})[/dim]"

        if status.ok:
            owner = child_owner_status(child)
            if owner.alive:
                owner_cell = f"[green]up[/green] [dim]:{owner.port}[/dim]"
            else:
                owner_cell = "[dim]down[/dim]"
        else:
            owner_cell = "[dim], [/dim]"

        git = "[dim]yes[/dim]" if status.is_git_repo else "[dim]no[/dim]"
        table.add_row(child.name, badge, owner_cell, git, display)
    console.print(table)
