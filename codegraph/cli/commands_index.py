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
    from codegraph.state.ipc import is_owner_alive, read_owner_port

    root = os.path.abspath(args.root)
    force = getattr(args, "force", False)

    console.print(LOGO)
    console.print(f"[dim]Repository:[/dim] [bold]{root}[/bold]\n")

    # Owner-aware routing: when an owner is alive it holds Kuzu's write lock
    # for its entire lifetime, so a direct index_repo() call would race the
    # lock and fail. Route through the owner's incremental_reindex MCP tool
    # instead — it re-checks every File node's blob SHA against HEAD,
    # re-indexes drifted files, and advances scan_meta.git_head. The --force
    # escape hatch preserves the old "fail loud if locked" behavior for
    # users who really want a fresh local lock attempt.
    if not force:
        owner_port = read_owner_port(root) if is_owner_alive(root) else None
        if owner_port:
            from codegraph.cli.commands_monitor import _ask_owner_incremental_reindex

            console.print(
                f"[dim]Owner alive on port {owner_port} — routing through "
                "incremental_reindex (drifted blobs only).[/dim]"
            )
            console.print(
                "[dim]For a full forced re-walk: stop the owner with "
                "[/dim][cyan]cgh serve --stop[/cyan][dim] then "
                "[/dim][cyan]cgh index --force[/cyan][dim].[/dim]\n"
            )
            stats = _ask_owner_incremental_reindex(root, owner_port)
            if stats is None:
                console.print(
                    Panel(
                        "[yellow]Owner reachable but incremental_reindex failed[/yellow] "
                        "(timeout or RPC error).\n"
                        "Check [cyan].codegraph/owner.log[/cyan], or rerun with "
                        "[cyan]cgh index --force[/cyan] after stopping the owner.",
                        title="[yellow]Index Failed[/yellow]",
                        border_style="yellow",
                    )
                )
                return
            _print_index_summary(stats)
            return

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

        try:
            stats = index_repo(
                root,
                on_file=on_file,
                on_discovery=on_discovery,
                method=getattr(args, "method", "auto"),
            )
        except RuntimeError as exc:
            if "Could not set lock" in str(exc):
                console.print()
                console.print(
                    Panel(
                        "[yellow]Database is locked by another cgh process.[/yellow]\n\n"
                        "An MCP owner is holding the Kuzu write lock for this repo.\n"
                        "Default [cyan]cgh index[/cyan] routes through the owner via MCP — "
                        "[cyan]--force[/cyan] was passed, which skips that path.\n\n"
                        "Either drop [cyan]--force[/cyan], or stop the owner first:\n"
                        "  [cyan]cgh serve --stop[/cyan]",
                        title="[yellow]Index Skipped[/yellow]",
                        border_style="yellow",
                    )
                )
                return
            raise

    _print_index_summary(stats)


def _print_index_summary(stats: dict) -> None:
    console.print()
    table = Table(box=box.ROUNDED, title="Index Summary", title_style="bold")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    indexed = stats.get("indexed", stats.get("reindexed_count", 0))
    skipped = stats.get("skipped", stats.get("unchanged_count", 0))
    deleted = stats.get("deleted_count", len(stats.get("deleted", [])) if isinstance(stats.get("deleted"), list) else 0)
    method = stats.get("method", stats.get("mode", "?"))
    table.add_row("Files indexed", f"[green]{indexed}[/green]")
    table.add_row("Files skipped", f"[dim]{skipped}[/dim]")
    if deleted:
        table.add_row("Files deleted", f"[yellow]{deleted}[/yellow]")
    errors = stats.get("errors", 0)
    table.add_row("Errors", f"[red]{errors}[/red]" if errors else "[dim]0[/dim]")
    elapsed = stats.get("elapsed_s", "?")
    table.add_row("Elapsed", f"[cyan]{elapsed}s[/cyan]")
    table.add_row("Method", f"[dim]{method}[/dim]")
    console.print(table)


# ---------------------------------------------------------------------------
# cmd_memory_index
# ---------------------------------------------------------------------------


def cmd_memory_index(args) -> None:
    """Scan the Claude Code memory directory into the FTS index."""
    from codegraph.claude_state.memory import scan_memory_dir

    root = os.path.abspath(args.root)
    console.print(LOGO)
    stats = scan_memory_dir(root, verbose=getattr(args, "verbose", False))

    from rich.table import Table

    table = Table(box=box.ROUNDED, title="Memory scan", title_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Memory dir", f"[dim]{stats['memory_dir']}[/dim]")
    table.add_row("Indexed", f"[green]{stats['indexed']}[/green]")
    table.add_row("Skipped", f"[dim]{stats['skipped']}[/dim]")
    table.add_row("Removed", f"[yellow]{stats['removed']}[/yellow]")
    console.print(table)


# ---------------------------------------------------------------------------
# cmd_plan_index
# ---------------------------------------------------------------------------


def cmd_plan_index(args) -> None:
    """Scan ~/.claude/plans/ into the FTS index."""
    from codegraph.claude_state.plans import scan_plan_dir

    root = os.path.abspath(args.root)
    console.print(LOGO)
    stats = scan_plan_dir(root, verbose=getattr(args, "verbose", False))

    from rich.table import Table

    table = Table(box=box.ROUNDED, title="Plan scan", title_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Plans dir", f"[dim]{stats['plans_dir']}[/dim]")
    table.add_row("Indexed", f"[green]{stats['indexed']}[/green]")
    table.add_row("Skipped", f"[dim]{stats['skipped']}[/dim]")
    table.add_row("Removed", f"[yellow]{stats['removed']}[/yellow]")
    console.print(table)


# ---------------------------------------------------------------------------
# cmd_watch
# ---------------------------------------------------------------------------


def cmd_watch(args) -> None:
    from codegraph.indexer import index_repo
    from codegraph.state.watcher import watch_forever

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

    # --background: spawn/reuse the owner, drop a persistent keepalive
    # marker (survives this process's exit), then return.
    if getattr(args, "background", False):
        from pathlib import Path

        from codegraph.state.ipc import (
            is_owner_alive,
            read_owner_port,
            register_keepalive,
            spawn_owner,
        )

        root_path = Path(root).resolve()
        register_keepalive(root_path)

        if is_owner_alive(root_path):
            port = read_owner_port(root_path)
            console.print(f"[green]Owner already running on port {port}[/green]")
        else:
            port = spawn_owner(
                root_path,
                watch=getattr(args, "watch", False),
                reindex=getattr(args, "reindex", False),
            )
            if port is None:
                console.print("[red]Failed to start owner (see .codegraph/owner.log)[/red]")
                return
            console.print(f"[green]Owner started on port {port}[/green]")

        console.print(
            "[dim]Background keepalive registered. "
            "Owner stays alive across Claude sessions.[/dim]\n"
            "[dim]Stop with:[/dim] [cyan]cgh serve --stop[/cyan] [dim]or[/dim] "
            "[cyan]pkill -f 'codegraph _serve_owner'[/cyan]"
        )
        return

    # --stop: kill owner + unregister this worker + remove keepalive marker.
    # Wait for the owner to actually exit so a follow-up `cgh serve` doesn't
    # race against a still-shutting-down uvicorn (would falsely report
    # "Owner already running on port X").
    if getattr(args, "stop", False):
        import time as _time
        from pathlib import Path

        from codegraph.state.ipc import (
            is_owner_alive,
            is_pid_alive,
            owner_pidfile,
            port_file,
            unregister_keepalive,
            unregister_worker,
        )

        root_path = Path(root).resolve()
        unregister_worker(root_path)
        unregister_keepalive(root_path)
        pf = owner_pidfile(root_path)
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 15)  # SIGTERM
                # Wait up to 5s for the process to die. The owner removes
                # its own pidfile/portfile via atexit on a clean exit.
                deadline = _time.time() + 5.0
                while _time.time() < deadline and is_pid_alive(pid):
                    _time.sleep(0.1)
                if is_pid_alive(pid):
                    os.kill(pid, 9)  # SIGKILL — escalate
                    _time.sleep(0.2)
                    console.print(f"[yellow]Owner (pid {pid}) force-killed after timeout.[/yellow]")
                else:
                    console.print(f"[green]Owner (pid {pid}) stopped.[/green]")
                # Belt-and-braces: drop stale ipc files if the owner crashed
                # without running its atexit.
                pf.unlink(missing_ok=True)
                port_file(root_path).unlink(missing_ok=True)
            except (ValueError, ProcessLookupError, PermissionError):
                console.print("[yellow]Owner already stopped.[/yellow]")
                pf.unlink(missing_ok=True)
                port_file(root_path).unlink(missing_ok=True)
        elif is_owner_alive(root_path):
            console.print("[yellow]Owner pidfile missing but port responds.[/yellow]")
        else:
            console.print("[dim]No owner running.[/dim]")
        return

    # Normal foreground serve (stdio proxy)
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
