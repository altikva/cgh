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


def cmd_hook_guard_codex(args: argparse.Namespace) -> None:
    """Codex variant of the guard: same decision, different protocol.
    Codex fires PreToolUse for shell commands only and reads a stdout
    JSON decision (exit 0 either way). Vendor docs disagree on the
    field spelling, so the deny carries both accepted forms."""
    from codegraph.state.guard import audit, check_tool_call, guard_mode

    mode = "assist"
    root = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = payload.get("cwd") or os.getcwd()
        from codegraph.core.config import find_codegraph_root

        root = find_codegraph_root(cwd)
        if root is None:
            return
        mode = guard_mode(root)
        reason = check_tool_call(
            root,
            str(payload.get("tool_name", "") or payload.get("tool", "")),
            payload.get("tool_input") or payload.get("arguments") or {},
            mode,
        )
        if reason:
            audit(root, f"deny (codex): {reason}")
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": reason,
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                )
            )
    except Exception as exc:
        if mode == "secure":
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": f"cgh guard failed closed (secure mode): {exc}",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "cgh guard failed closed",
                    }
                )
            )
            return
        if root is not None:
            audit(root, f"guard error, failing open (assist, codex): {exc}")


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

    from codegraph.integrations.base import all_integrations

    console.print("[bold]agents:[/bold]")
    badges = {
        "enforce": "[green]enforce[/green]",
        "partial": "[yellow]partial[/yellow]",
        "advisory": "[yellow]advisory[/yellow]",
        "none": "[red]unprotected[/red]",
    }
    missing: list[str] = []
    for integration in all_integrations():
        if not integration.detect(root):
            continue
        spec = integration.guard_spec()
        if spec.level == "none":
            level = "none"
        elif integration.guard_installed(root):
            level = spec.level
        else:
            level = "none"
            missing.append(integration.name)
        note = f"  [dim]{spec.note}[/dim]" if spec.note and level != "none" else ""
        console.print(f"  {integration.display:<12} {badges[level]}{note}")
    for name in missing:
        console.print(
            f"[dim]Install the {name} guard hook with[/dim] "
            f"[cyan]cgh setup {name}[/cyan]"
        )
    console.print(
        "[dim]An agent listed unprotected can read anything its own tools "
        "allow; the only barrier there is cgh's MCP-side gate.[/dim]"
    )
