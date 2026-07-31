# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands for the `cgh hooks` verbs:
#              install / uninstall / status. Manages the git hooks
#              (post-merge, post-checkout, post-rewrite) that keep the code
#              graph fresh after a pull, merge, branch switch, or rebase.

from __future__ import annotations

import argparse

import os
from pathlib import Path

from codegraph.cli import LOGO, console
from codegraph.state.git_hooks import (
    HOOK_EVENTS,
    git_hooks_status,
    hooks_target_info,
    install_git_hooks,
    uninstall_git_hooks,
)


def cmd_githooks(args: argparse.Namespace) -> None:
    """Dispatcher for `cgh hooks <verb>`."""
    action = getattr(args, "action", None) or "status"
    root = Path(os.path.abspath(args.root))

    if action == "install":
        return _install(root, allow_shared=getattr(args, "shared", False))
    if action == "uninstall":
        return _uninstall(root)
    if action == "status":
        return _status(root)
    console.print(f"[red]Unknown action: {action}[/red]")
    console.print("[dim]Usage: cgh hooks install | uninstall | status[/dim]")


def _install(root: Path, allow_shared: bool = False) -> None:
    target, is_shared = hooks_target_info(root)
    if target is None:
        console.print(
            "[yellow]Not a git repository (no hooks directory found).[/yellow]\n"
            "[dim]Run this inside a git repo, or run [/dim][cyan]git init[/cyan][dim] first.[/dim]"
        )
        return
    if is_shared and not allow_shared:
        console.print(
            f"[yellow]core.hooksPath points outside this repo:[/yellow] [dim]{target}[/dim]\n"
            "That is a shared hooks directory used by every repo on this machine, so "
            "cgh will not write there automatically.\n"
            "[dim]The reindex hook is repo-scoped (it no-ops where there is no .codegraph/), "
            "so if you want it in the shared dir anyway, run "
            "[/dim][cyan]cgh hooks install --shared[/cyan][dim]. Otherwise unset the path for "
            "this repo with [/dim][cyan]git config --unset core.hooksPath[/cyan][dim] and retry.[/dim]"
        )
        return

    written = install_git_hooks(root)
    console.print("[green]Installed git hooks[/green] for code-graph refresh:")
    for event in written:
        console.print(f"  [green]+[/green] {event}")
    console.print(
        "\n[dim]These run a backgrounded incremental reindex after a pull, "
        "merge, branch switch, or rebase, so the graph stays accurate.[/dim]"
    )


def _uninstall(root: Path) -> None:
    touched = uninstall_git_hooks(root)
    if not touched:
        console.print("[dim]No cgh git hooks were installed.[/dim]")
        return
    console.print("[green]Removed cgh git hooks:[/green]")
    for event in touched:
        console.print(f"  [green]-[/green] {event}")


def _status(root: Path) -> None:
    console.print(LOGO)
    status = git_hooks_status(root)
    any_on = any(status.values())
    for event in HOOK_EVENTS:
        on = status.get(event, False)
        badge = "[green]installed[/green]" if on else "[dim]not installed[/dim]"
        console.print(f"  {event:<16} {badge}")
    if not any_on:
        console.print(
            "\n[dim]Install them with[/dim] [cyan]cgh hooks install[/cyan] "
            "[dim]so the graph refreshes after git pulls and merges.[/dim]"
        )
