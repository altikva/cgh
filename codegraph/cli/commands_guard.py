# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh guard status|sync` plus the hidden `_hook_guard`
#              handler wired into agent pre-tool-use hooks: reads the
#              hook payload on stdin, checks the finding store, exit 2
#              with a reason denies, exit 0 allows. Fail posture follows
#              the mode: assist fails open with a logged warning, secure
#              fails closed.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def cmd_hook_guard(args: argparse.Namespace) -> None:
    """Decide one agent tool call. Speed matters: this runs on every
    Read/Grep/Glob/Bash of a hooked agent."""
    from codegraph.state.guard import audit, check_tool_call, guard_mode

    mode = "assist"
    root = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = payload.get("cwd") or os.getcwd()
        from codegraph.core.config import find_codegraph_root

        root = find_codegraph_root(cwd)
        if root is None:
            return  # not a cgh repo, nothing to guard
        mode = guard_mode(root)
        reason = check_tool_call(
            root,
            str(payload.get("tool_name", "")),
            payload.get("tool_input") or {},
            mode,
        )
        if reason:
            audit(root, f"deny: {reason}")
            print(reason, file=sys.stderr)
            sys.exit(2)
    except SystemExit:
        raise
    except Exception as exc:
        if mode == "secure":
            # A broken guard reads as blocked, never as leaked.
            print(
                f"cgh guard failed closed (secure mode): {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)
        if root is not None:
            audit(root, f"guard error, failing open (assist): {exc}")


def cmd_guard(args: argparse.Namespace) -> None:
    from rich.console import Console

    from codegraph.state.guard import blocking_paths, guard_mode, sync_static_rules

    console = Console()
    root = Path(os.path.abspath(args.root))
    action = getattr(args, "action", "status")

    if action == "sync":
        added, removed = sync_static_rules(root)
        if guard_mode(root) != "secure":
            console.print(
                "[dim]Static deny rules only apply in secure mode "
                '(mode = "secure" under [codegraph]).[/dim]'
            )
        else:
            console.print(
                f"[green]+[/green] static deny rules synced "
                f"({added} added, {removed} removed)."
            )
        return

    # status
    mode = guard_mode(root)
    barred = blocking_paths(root)
    console.print(
        f"[bold]mode:[/bold] {mode}"
        + (
            "  [red](fail-closed)[/red]"
            if mode == "secure"
            else "  [dim](fail-open)[/dim]"
        )
    )
    console.print(f"[bold]flagged files:[/bold] {len(barred)}")

    hook_installed = _claude_guard_hook_installed(root)
    agents = [
        ("Claude Code", "enforce" if hook_installed else "unprotected"),
        ("Gemini CLI", "unprotected"),
        ("Codex CLI", "unprotected"),
    ]
    console.print("[bold]agents:[/bold]")
    badges = {
        "enforce": "[green]enforce[/green]",
        "advisory": "[yellow]advisory[/yellow]",
        "unprotected": "[red]unprotected[/red]",
    }
    for name, level in agents:
        console.print(f"  {name:<12} {badges[level]}")
    if not hook_installed:
        console.print(
            "[dim]Install the Claude Code guard hook with[/dim] "
            "[cyan]cgh setup claude[/cyan]"
        )
    console.print(
        "[dim]An agent listed unprotected can read anything its own tools "
        "allow; the only barrier there is cgh's MCP-side gate.[/dim]"
    )


def _claude_guard_hook_installed(root: Path) -> bool:
    for name in ("settings.local.json", "settings.json"):
        p = root / ".claude" / name
        try:
            if p.exists() and "cgh-guard" in p.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False
