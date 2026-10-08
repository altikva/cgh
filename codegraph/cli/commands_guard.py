# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh guard` plus the hidden `_hook_guard` /
#              `_hook_guard_codex` hook entry points. The guard was
#              removed in 0.15.0: the hook commands stay because agent
#              configs written by older versions still call them, and
#              they always allow. `cgh guard` prints a deprecation note
#              and removes the deny entries and hooks cgh wrote earlier.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _drain_stdin() -> None:
    """Read the hook payload so the agent never sees a broken pipe."""
    try:
        sys.stdin.read()
    except Exception:
        pass


def cmd_hook_guard(args: argparse.Namespace) -> None:
    """Allow-only shim for the Claude Code / Gemini CLI hook: exit 0,
    print nothing."""
    _drain_stdin()


def cmd_hook_guard_codex(args: argparse.Namespace) -> None:
    """Allow-only shim for the Codex hook: no stdout decision means the
    command proceeds."""
    _drain_stdin()


def print_cleanup_report(console, report) -> None:
    """One line per kind of leftover removed; nothing when clean."""
    if report.claude_rules:
        console.print(
            f"  [green]-[/green] removed {len(report.claude_rules)} deny rule(s) "
            "cgh had written to .claude/settings.local.json"
        )
    if report.bobignore_lines:
        console.print("  [green]-[/green] removed cgh's managed block from .bobignore")
    for path in report.hooks:
        console.print(f"  [green]-[/green] removed the cgh guard hook from {path}")
    if report.sidecar_removed and not report.claude_rules:
        console.print("  [green]-[/green] removed .codegraph/guard_denies.json")


def cmd_guard(args: argparse.Namespace) -> None:
    from rich.console import Console

    from codegraph.state.guard import cleanup_guard_leftovers

    console = Console()
    root = Path(os.path.abspath(args.root))
    console.print(
        "[yellow]cgh guard is deprecated:[/yellow] since 0.15.0 cgh no longer "
        "blocks agent file access. Use your agent's own permission rules "
        "(for example Claude Code permissions.deny) to keep files out of reach."
    )
    report = cleanup_guard_leftovers(root)
    if report.changed:
        print_cleanup_report(console, report)
    else:
        console.print("[dim]No guard leftovers to clean up.[/dim]")
