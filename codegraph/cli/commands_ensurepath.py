# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh ensurepath`. Adds the dir holding the cgh executable to the
#              shell profile so the `cgh` command is found in new shells. Like
#              `pipx ensurepath`. On native Windows it prints the command to
#              run (the registry edit is left to install.ps1).

from __future__ import annotations

import argparse

import sys
from pathlib import Path

from codegraph.cli import console
from codegraph.state import ensurepath as ep


def cmd_ensurepath(args: argparse.Namespace) -> None:
    scripts = ep.scripts_dir()

    if ep.is_on_path(scripts):
        console.print(
            f"[green]cgh is already on your PATH[/green] [dim]({scripts})[/dim]"
        )
        return

    env = ep.detect_env()

    if env == "windows":
        # Editing the registry PATH from here is risky; print the one-liner
        # instead (install.ps1 does the edit for PowerShell users). Do NOT use
        # setx: it copies the full process PATH (system + user) into the user
        # PATH and truncates at 1024 chars. The read-modify-write below touches
        # only the user PATH and never truncates.
        console.print("[bold]Add cgh to your PATH (Windows)[/bold]")
        console.print("  Run this in PowerShell, then open a new terminal:\n")
        console.print(
            "  [cyan]$p = [Environment]::GetEnvironmentVariable('Path','User'); "
            f'if ($p -notlike "*{scripts}*") {{ [Environment]::SetEnvironmentVariable('
            f"'Path', \"$p;{scripts}\", 'User') }}[/cyan]"
        )
        return

    value = ep.path_value_for(env, scripts)
    profile = ep.shell_profile()

    if not getattr(args, "yes", False) and sys.stdin.isatty():
        try:
            answer = (
                console.input(
                    f"Add cgh to PATH by appending to [cyan]{profile}[/cyan]? [Y/n] "
                )
                .strip()
                .lower()
            )
        except EOFError:
            answer = "n"
        if answer in ("n", "no"):
            console.print(
                "[dim]Skipped. Re-run [/dim][cyan]python -m cgh ensurepath[/cyan]"
                "[dim] anytime, or use [/dim][cyan]python -m cgh[/cyan][dim] directly.[/dim]"
            )
            return

    result = ep.append_to_profile(Path(profile), value)
    if result == "already":
        console.print(f"[dim]Already configured in {profile}.[/dim]")
    else:
        console.print(f"  [green]+[/green] added cgh to PATH in [cyan]{profile}[/cyan]")
    console.print(
        f"[dim]Run [/dim][cyan]source {profile}[/cyan][dim] or open a new terminal, "
        "then [/dim][cyan]cgh --version[/cyan][dim].[/dim]"
    )
