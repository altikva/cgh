# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands — init, setup, parsers.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, console

# Wildcards that cover every codegraph MCP tool (current + future). The
# two-form shape matches what both older and current Claude Code builds
# accept.
_CODEGRAPH_ALLOW_PATTERNS = ("mcp__codegraph", "mcp__codegraph__*")


def _configure_claude_auto_accept(root: Path) -> list[str]:
    """
    Add codegraph MCP wildcards to .claude/settings.local.json so
    Claude Code doesn't prompt for every tool call. Also removes any
    redundant per-tool entries previously added via the "don't ask
    again" dialog. Returns the list of patterns that were newly added.
    """
    import json as _json

    settings_dir = root / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    if settings_path.exists():
        try:
            data = _json.loads(settings_path.read_text() or "{}")
        except Exception:
            data = {}
    else:
        data = {}

    allow = data.setdefault("permissions", {}).setdefault("allow", [])

    # Drop redundant per-tool entries
    allow[:] = [item for item in allow if not (item.startswith("mcp__codegraph__") and item != "mcp__codegraph__*")]

    # Add wildcards if missing
    added: list[str] = []
    for pattern in _CODEGRAPH_ALLOW_PATTERNS:
        if pattern not in allow:
            allow.append(pattern)
            added.append(pattern)

    settings_path.write_text(_json.dumps(data, indent=2) + "\n")
    return added


def _incremental_via_owner(
    root: Path,
    port: int | None,
    console_obj,
    tool_name: str = "incremental_reindex",
    overall_timeout: int = 900,
) -> None:
    """
    Call the running MCP owner to run a scan, while live-tailing
    .codegraph/activity.log so the user sees progress instead of
    staring at a frozen prompt.

    Runs the HTTP POST in a background thread with a generous timeout
    (15 min). The main thread renders a Rich Live view polling the
    activity log every 500ms. Either branch finishing the scan wins.
    """
    if not port:
        console_obj.print("  [yellow]owner port not known — skipping[/yellow]")
        return

    import http.client
    import json as _json
    import threading
    import time as _t

    from rich.live import Live
    from rich.table import Table

    from codegraph.activity import tail as _act_tail
    from codegraph.auth import ensure_auth_key

    token = ensure_auth_key(root)
    body = _json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
    )

    result_holder: dict = {"status": None, "body": None, "error": None}
    done = threading.Event()

    def _call() -> None:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=overall_timeout)
            c.request(
                "POST",
                "/mcp",
                body=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            resp = c.getresponse()
            result_holder["status"] = resp.status
            result_holder["body"] = resp.read().decode("utf-8", errors="replace")
            c.close()
        except Exception as exc:
            result_holder["error"] = str(exc)
        finally:
            done.set()

    t = threading.Thread(target=_call, daemon=True)
    t.start()

    def _render() -> Table:
        entries = _act_tail(root, n=8)
        tbl = Table(
            title=f"[bold cyan]owner:[/bold cyan] {tool_name} in progress…",
            title_style="",
            expand=False,
        )
        tbl.add_column("when", style="dim", width=10)
        tbl.add_column("event", width=16)
        tbl.add_column("detail", overflow="fold")
        if not entries:
            tbl.add_row("—", "[dim]waiting[/dim]", "[dim]activity log empty[/dim]")
        now = _t.time()
        for ts, event, detail in entries:
            age = now - ts
            when = f"{int(age)}s ago" if age < 60 else f"{int(age / 60)}m ago"
            style = "green" if event.endswith("_end") else ("yellow" if "error" in event else "cyan")
            tbl.add_row(when, f"[{style}]{event}[/{style}]", detail)
        return tbl

    try:
        with Live(_render(), console=console_obj, refresh_per_second=2) as live:
            while not done.is_set():
                _t.sleep(0.5)
                live.update(_render())
    except KeyboardInterrupt:
        console_obj.print("\n  [yellow]stopped watching — owner may still be working in the background[/yellow]")
        return

    # Report result
    if result_holder["error"]:
        console_obj.print(
            f"  [yellow]owner call failed:[/yellow] {result_holder['error']}  "
            "[dim](the owner itself may have completed — check `cgh status`)[/dim]"
        )
        return
    if result_holder["status"] != 200:
        console_obj.print(f"  [yellow]owner returned HTTP {result_holder['status']}[/yellow]")
        return

    # Try to pull the JSON stats out of the MCP response
    try:
        payload = _json.loads(result_holder["body"] or "{}")
        content = (payload.get("result") or {}).get("content") or []
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        if text:
            inner = _json.loads(text)
            reindexed = inner.get("reindexed_count") or inner.get("indexed") or 0
            deleted = inner.get("deleted_count") or 0
            elapsed = inner.get("elapsed_s") or inner.get("elapsed") or "?"
            console_obj.print(
                f"  [green]+[/green] owner completed: reindexed={reindexed}, deleted={deleted}, elapsed={elapsed}s"
            )
            return
    except Exception:
        pass
    console_obj.print("  [green]+[/green] owner completed the scan")


def _detect_existing_state(root: Path) -> dict:
    """
    Probe the project for existing codegraph state so `cgh init` can
    choose the right index strategy and warn about stale artifacts.
    """
    cg_dir = root / ".codegraph"
    graph_db = cg_dir / "graph.db"
    fts_db = cg_dir / "fts.db"

    state = {
        "initialized": cg_dir.exists(),
        "graph_db_bytes": 0,
        "fts_db_bytes": 0,
        "indexed_files": 0,
        "owner_alive": False,
        "owner_pid": None,
        "owner_port": None,
        "scan_meta": None,
        "extra_dirs": [],
        "agent_blocks": {},  # tool -> True if the codegraph-usage block is already there
        "mcp_server_configured": False,
    }

    if graph_db.exists():
        try:
            state["graph_db_bytes"] = graph_db.stat().st_size
        except OSError:
            pass
    if fts_db.exists():
        try:
            state["fts_db_bytes"] = fts_db.stat().st_size
        except OSError:
            pass

    # File count from the graph (best-effort, readonly — works even if
    # the owner holds the write lock)
    try:
        from codegraph.core.db import get_readonly_connection

        conn = get_readonly_connection(root)
        if conn is not None:
            try:
                r = conn.execute("MATCH (f:File) RETURN count(f) AS c")
                state["indexed_files"] = int(r.get_next()[0])
            except Exception:
                pass
    except Exception:
        pass

    # Owner status
    try:
        from codegraph.ipc import is_owner_alive, read_owner_pid, read_owner_port

        state["owner_alive"] = is_owner_alive(root)
        state["owner_pid"] = read_owner_pid(root)
        state["owner_port"] = read_owner_port(root)
    except Exception:
        pass

    # Scan meta (last indexed sha + branch)
    try:
        from codegraph.scan_meta import read_meta

        state["scan_meta"] = read_meta(root)
    except Exception:
        pass

    # extra_dirs from config.toml
    try:
        import tomllib

        cfg = cg_dir / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                state["extra_dirs"] = tomllib.load(f).get("codegraph", {}).get("extra_dirs", [])
    except Exception:
        pass

    # Existing codegraph-usage blocks in agent root files
    marker = "<!-- codegraph-usage:start -->"
    for tool_key, rel in (
        ("claude", "CLAUDE.md"),
        ("codex", "AGENTS.md"),
        ("gemini", "GEMINI.md"),
        ("cursor", ".cursor/rules/codegraph-usage.mdc"),
    ):
        fp = root / rel
        if fp.exists():
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                state["agent_blocks"][tool_key] = marker in content or tool_key == "cursor"
            except OSError:
                state["agent_blocks"][tool_key] = False

    # Skill files + user modifications
    state["claude_skills_installed"] = []
    state["claude_skills_modified"] = []
    skills_dir = root / ".claude" / "skills"
    if skills_dir.exists():
        try:
            state["claude_skills_installed"] = sorted(
                d.name for d in skills_dir.iterdir() if d.is_dir() and d.name.startswith("cgh-")
            )
        except OSError:
            pass
        try:
            from codegraph.skill_installer import detect_modified_skills

            state["claude_skills_modified"] = detect_modified_skills(root)
        except Exception:
            pass

    # MCP server config
    mcp_path = root / ".mcp.json"
    if mcp_path.exists():
        try:
            import json as _json

            data = _json.loads(mcp_path.read_text())
            state["mcp_server_configured"] = "codegraph" in (data.get("mcpServers") or {})
        except Exception:
            pass

    return state


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


def cmd_init(args) -> None:
    import glob
    import shutil

    import questionary
    from questionary import Style

    from codegraph.config import init_project

    root = Path(os.path.abspath(args.root))
    console.print(LOGO)
    console.print(f"  [dim]Project:[/dim] [bold]{root}[/bold]\n")

    cg_style = Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "fg:white bold"),
            ("answer", "fg:green bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
            ("separator", "fg:cyan"),
            ("instruction", "fg:white dim"),
            ("text", "fg:white"),
        ]
    )

    # -- Step 0: Probe existing state (before anything mutates disk) --
    prior_state = _detect_existing_state(root)
    if prior_state["initialized"]:
        console.print("  [bold]Existing codegraph state detected:[/bold]\n")
        bits: list[str] = []
        if prior_state["indexed_files"] > 0:
            bits.append(f"{prior_state['indexed_files']:,} files indexed")
        if prior_state["graph_db_bytes"] > 0:
            bits.append(f"graph.db {prior_state['graph_db_bytes'] // 1024} KB")
        if prior_state["owner_alive"]:
            bits.append(
                f"[green]owner running[/green] (pid {prior_state['owner_pid']} port {prior_state['owner_port']})"
            )
        elif prior_state["owner_pid"]:
            bits.append(f"[yellow]stale owner.pid {prior_state['owner_pid']}[/yellow]")
        if prior_state["scan_meta"] and prior_state["scan_meta"].get("git_head"):
            sha = prior_state["scan_meta"]["git_head"][:8]
            branch = prior_state["scan_meta"].get("git_branch") or "?"
            bits.append(f"last scan at {sha} on {branch}")
        if prior_state["extra_dirs"]:
            bits.append(f"{len(prior_state['extra_dirs'])} extra_dirs")
        if prior_state["mcp_server_configured"]:
            bits.append(".mcp.json already has codegraph")
        if prior_state.get("claude_skills_installed"):
            n = len(prior_state["claude_skills_installed"])
            mod = len(prior_state.get("claude_skills_modified") or [])
            label = f"{n} claude skill{'s' if n != 1 else ''}"
            if mod:
                label += f" ([yellow]{mod} modified locally[/yellow])"
            bits.append(label)
        blocks_present = [k for k, v in prior_state["agent_blocks"].items() if v]
        if blocks_present:
            bits.append("agent blocks: " + ", ".join(blocks_present))
        for b in bits:
            console.print(f"    • {b}")
        if not bits:
            console.print("    [dim](initialized but empty — safe to full scan)[/dim]")
        console.print()

    # -- Step 1: Create .codegraph/ --
    with console.status("[bold cyan]Setting up codegraph...", spinner="dots"):
        result = init_project(root)

    if result["created"]:
        for f in result["created"]:
            console.print(f"  [green]+[/green] {f}")
    else:
        console.print("  [dim].codegraph/ already exists[/dim]")

    console.print()

    # -- Step 2: Detect AI tools --
    console.print("  [bold]Detecting AI tools...[/bold]\n")

    all_tools = [
        ("Claude Code", "claude", (root / ".claude").exists() or shutil.which("claude") is not None),
        ("Cursor", "cursor", (root / ".cursor").exists() or (root / ".cursorrules").exists()),
        ("Codex CLI", "codex", (root / "AGENTS.md").exists() or shutil.which("codex") is not None),
        (
            "Gemini CLI",
            "gemini",
            (root / "GEMINI.md").exists() or (root / ".gemini").exists() or shutil.which("gemini") is not None,
        ),
    ]

    for name, _, detected in all_tools:
        icon = "[green]>[/green]" if detected else "[dim]-[/dim]"
        status = "[green]detected[/green]" if detected else "[dim]not found[/dim]"
        console.print(f"    {icon} {name:15s} {status}")

    console.print()
    detected_tools = [(name, key) for name, key, detected in all_tools if detected]

    # -- Step 3: Select which tools to configure --
    selected_keys = []
    if detected_tools and not args.yes:
        choices = [
            questionary.Choice(
                title=f"{name}  (MCP server + hooks)",
                value=key,
                checked=True,
            )
            for name, key in detected_tools
        ]
        # Also offer non-detected tools as unchecked
        for name, key, detected in all_tools:
            if not detected:
                choices.append(
                    questionary.Choice(
                        title=f"{name}  [not detected]",
                        value=key,
                        checked=False,
                    )
                )

        selected_keys = questionary.checkbox(
            "Install MCP server for:",
            choices=choices,
            style=cg_style,
            instruction="(space to toggle, enter to confirm)",
        ).ask()

        if selected_keys is None:
            selected_keys = []
    elif args.yes:
        selected_keys = [key for _, key in detected_tools]

    # Show which tools will be skipped (explicit — no silent generation)
    all_keys = [k for _, k, _ in all_tools]
    skipped = [k for k in all_keys if k not in selected_keys]
    if skipped:
        console.print(
            "  [dim]skipping:[/dim] "
            + ", ".join(f"[dim]{k}[/dim]" for k in skipped)
            + "  [dim](no config, no agent block, no skills)[/dim]\n"
        )

    # Check for locally-edited skills before overwriting
    overwrite_skills = True
    if "claude" in selected_keys:
        from codegraph.skill_installer import detect_modified_skills

        modified = detect_modified_skills(root)
        if modified and not args.yes:
            console.print("  [yellow]You have locally-edited skills in .claude/skills/:[/yellow]")
            for m in modified:
                console.print(f"    • [yellow]{m}[/yellow] (SKILL.md differs from bundled)")
            overwrite_skills = questionary.confirm(
                "Overwrite with the bundled versions? (Your edits will be lost)",
                default=False,
                style=cg_style,
            ).ask()
            if not overwrite_skills:
                console.print("  [green]Keeping your edits[/green] — will refresh only new / unmodified skills.\n")

    for key in selected_keys:
        _install_integration(root, key, overwrite_skills=overwrite_skills)

    if selected_keys:
        console.print()

    # -- Step 3b: offer to auto-accept codegraph MCP tools in Claude Code --
    if "claude" in selected_keys:
        auto_accept = (
            args.yes
            or questionary.confirm(
                "Auto-accept codegraph MCP tool calls in Claude Code (skip the per-call permission prompt)?",
                default=True,
                style=cg_style,
            ).ask()
        )
        if auto_accept:
            added = _configure_claude_auto_accept(root)
            if added:
                console.print(
                    f"    [green]+[/green] .claude/settings.local.json "
                    f"[dim](permissions.allow += {', '.join(added)})[/dim]"
                )
            else:
                console.print("    [dim]•[/dim] .claude/settings.local.json [dim](already allows codegraph)[/dim]")
            console.print()

    # -- Step 3c: offer to inject codegraph usage guidelines into agent rules --
    if selected_keys:
        from codegraph.skill_installer import install_usage_guidelines

        target_files = {
            "claude": "CLAUDE.md",
            "codex": "AGENTS.md",
            "gemini": "GEMINI.md",
            "cursor": ".cursor/rules/codegraph-usage.mdc",
        }
        inject_targets = [(k, target_files[k]) for k in selected_keys if k in target_files]
        if inject_targets:
            targets_str = ", ".join(f for _, f in inject_targets)
            inject = (
                args.yes
                or questionary.confirm(
                    f"Append codegraph usage guidelines to {targets_str}? "
                    "(Helps agents pick the right tool instead of reading files blindly)",
                    default=True,
                    style=cg_style,
                ).ask()
            )
            if inject:
                for tool_key, label in inject_targets:
                    written = install_usage_guidelines(root, tool_key)
                    if written:
                        rel = written.replace(str(root) + "/", "")
                        console.print(f"    [green]+[/green] {rel} [dim](codegraph usage block)[/dim]")
                console.print()

    # -- Step 4: Detect parseable files --
    # Use git ls-files to match what the real indexer will process
    # (respects .gitignore). Fall back to glob if not a git repo.
    from codegraph.parsers import get_parser_info

    parsers = get_parser_info()
    ext_to_lang = {ext: info["lang"] for info in parsers for ext in info["extensions"]}

    file_counts: dict[str, int] = {}
    try:
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                suffix = Path(line).suffix.lower()
                lang = ext_to_lang.get(suffix)
                if lang:
                    file_counts[lang] = file_counts.get(lang, 0) + 1
        else:
            raise RuntimeError("git ls-files failed")
    except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError, OSError):
        # Fallback — glob from project root (will overcount but better than nothing)
        for info in parsers:
            count = 0
            for ext in info["extensions"]:
                count += len(glob.glob(f"**/*{ext}", root_dir=str(root), recursive=True))
            if count > 0:
                file_counts[info["lang"]] = count

    if file_counts:
        console.print("  [bold]Files to index:[/bold]\n")
        lang_colors = {
            "python": "green",
            "typescript": "blue",
            "javascript": "yellow",
            "terraform": "magenta",
            "markdown": "cyan",
            "vue": "green",
            "nuxt_config": "green",
        }
        for lang, count in sorted(file_counts.items(), key=lambda x: -x[1]):
            color = lang_colors.get(lang, "white")
            bar_len = min(count // 5, 30) or 1
            bar = f"[{color}]{'>' * bar_len}[/{color}]"
            console.print(f"    [{color}]{lang:12s}[/{color}] {count:>5d} files  {bar}")
        console.print()

    total = sum(file_counts.values())

    # -- Step 5: Index now? — branches on prior state --
    if total > 0:
        owner_alive = prior_state.get("owner_alive", False)
        has_data = prior_state.get("indexed_files", 0) > 0
        default_method = "auto"
        choice = None

        if owner_alive:
            console.print("  [yellow]The MCP owner is running — it already watches this repo.[/yellow]")
            if not args.yes:
                choice = (
                    questionary.select(
                        "What do you want to do?",
                        choices=[
                            questionary.Choice(title="Skip — owner keeps the index fresh", value="skip"),
                            questionary.Choice(
                                title="Incremental rescan through the owner (no lock fight)",
                                value="mcp_scan",
                            ),
                            questionary.Choice(
                                title="Stop owner, wipe DB, full scan  [destructive]",
                                value="reset",
                            ),
                        ],
                        style=cg_style,
                    ).ask()
                    or "skip"
                )
            else:
                choice = "skip"
        elif has_data:
            if not args.yes:
                choice = (
                    questionary.select(
                        f"Index already has {prior_state['indexed_files']:,} files. Action?",
                        choices=[
                            questionary.Choice(
                                title="Incremental (only files changed since last scan)",
                                value="incremental",
                            ),
                            questionary.Choice(title="Full scan (re-parse everything)", value="full"),
                            questionary.Choice(title="Skip — keep as-is", value="skip"),
                        ],
                        style=cg_style,
                    ).ask()
                    or "incremental"
                )
            else:
                choice = "incremental"
        else:
            if not args.yes:
                do_full = questionary.confirm(
                    f"Run full scan of {total} files now?",
                    default=True,
                    style=cg_style,
                ).ask()
                choice = "full" if do_full else "skip"
            else:
                choice = "full"

        console.print()
        if choice == "full":
            from codegraph.cli.commands_index import cmd_index

            cmd_index(argparse.Namespace(root=str(root), verbose=False, method=default_method))
        elif choice == "incremental":
            from codegraph.cli.commands_index import cmd_index

            cmd_index(argparse.Namespace(root=str(root), verbose=False, method="incremental"))
        elif choice == "mcp_scan":
            _incremental_via_owner(
                root=root,
                port=prior_state.get("owner_port"),
                console_obj=console,
            )
        elif choice == "reset":
            from codegraph.cli.commands_monitor import cmd_reset

            cmd_reset(argparse.Namespace(root=str(root), yes=True, drop_extra_dirs=False, no_reindex=False))
        else:
            console.print("  [dim]Run 'cgh index' when ready.[/dim]")
    else:
        console.print("  [dim]No parseable files found. Run 'codegraph parsers' to see supported languages.[/dim]")

    # -- Done --
    console.print()
    console.print(
        Panel(
            "[bold]codegraph is ready![/bold]\n\n"
            "  [cyan]cgh stats[/cyan]         View graph statistics\n"
            "  [cyan]cgh search X[/cyan]      Find symbols\n"
            "  [cyan]cgh serve[/cyan]         Start MCP server\n"
            "  [cyan]cgh parsers[/cyan]       List supported languages\n"
            "  [cyan]cgh --help[/cyan]        All commands",
            border_style="green",
        )
    )


def _install_integration(root: Path, tool: str, overwrite_skills: bool = True) -> None:
    """Install MCP config + hooks for an AI tool."""
    import json as _json
    import shutil

    # Prefer `cgh` (short alias), fall back to `codegraph`, then python -m
    if shutil.which("cgh"):
        mcp_entry = {
            "command": "cgh",
            "args": ["serve", "--root", ".", "--watch", "--reindex"],
        }
    elif shutil.which("codegraph"):
        mcp_entry = {
            "command": "codegraph",
            "args": ["serve", "--root", ".", "--watch", "--reindex"],
        }
    else:
        mcp_entry = {
            "command": sys.executable,
            "args": ["-m", "codegraph", "serve", "--root", ".", "--watch", "--reindex"],
        }

    from codegraph.skill_installer import (
        install_claude,
        install_codex,
        install_cursor,
        install_gemini,
    )

    def _skills_line(tool_label: str, names: list[str]) -> None:
        if names:
            plural = "s" if len(names) != 1 else ""
            joined = ", ".join(names)
            console.print(f"    [green]+[/green] {tool_label} [dim]({len(names)} skill{plural}: {joined})[/dim]")

    if tool == "claude":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text())
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n")
        console.print("    [green]+[/green] .mcp.json [dim](MCP server)[/dim]")

        # Claude hooks
        settings_dir = root / ".claude"
        settings_dir.mkdir(exist_ok=True)
        settings_path = settings_dir / "settings.json"
        if settings_path.exists():
            settings = _json.loads(settings_path.read_text())
        else:
            settings = {}

        # Check if post-commit hook already exists
        post_hooks = settings.get("hooks", {}).get("PostToolUse", [])
        has_codegraph_hook = any(
            "codegraph" in str(h.get("hooks", [{}])[0].get("command", ""))
            for h in post_hooks
            if h.get("matcher") == "Bash(git commit*)"
        )
        if not has_codegraph_hook:
            console.print("    [green]+[/green] .claude/settings.json [dim](post-commit hook)[/dim]")

        # Skills — may preserve local edits if the user said so
        _skills_line(".claude/skills/", install_claude(root, overwrite_modified=overwrite_skills))

    elif tool == "cursor":
        cursor_dir = root / ".cursor"
        cursor_dir.mkdir(exist_ok=True)
        mcp_path = cursor_dir / "mcp.json"
        data = {"mcpServers": {"codegraph": mcp_entry}}
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n")
        console.print("    [green]+[/green] .cursor/mcp.json [dim](MCP server)[/dim]")
        _skills_line(".cursor/rules/", install_cursor(root))

    elif tool == "codex":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text())
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n")
        console.print("    [green]+[/green] .mcp.json [dim](MCP server for Codex)[/dim]")
        _skills_line("AGENTS.md", install_codex(root))

    elif tool == "gemini":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text())
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n")
        console.print("    [green]+[/green] .mcp.json [dim](MCP server for Gemini)[/dim]")
        _skills_line("GEMINI.md", install_gemini(root))


# ---------------------------------------------------------------------------
# cmd_parsers
# ---------------------------------------------------------------------------


def cmd_parsers(args) -> None:
    from codegraph.parsers import get_parser_info, get_supported_extensions

    console.print(LOGO)

    table = Table(
        title="Registered Parsers",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    table.add_column("Language", style="bold")
    table.add_column("Extensions")
    table.add_column("Extracts", style="dim")
    table.add_column("Description", style="dim", max_width=40)

    colors = {
        "python": "green",
        "typescript": "blue",
        "terraform": "magenta",
        "markdown": "cyan",
        "rust": "red",
        "go": "blue",
        "java": "yellow",
    }

    for info in get_parser_info():
        lang = info["lang"]
        color = colors.get(lang, "white")
        exts = " ".join(f"[{color}]{e}[/{color}]" for e in info["extensions"])
        extracts = ", ".join(info["extracts"][:5])
        table.add_row(
            f"[{color}]{lang}[/{color}]",
            exts,
            extracts,
            info.get("description", ""),
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(get_supported_extensions())} file extensions supported[/dim]")
    console.print("\n[dim]To add a new parser: create a file in codegraph/parsers/[/dim]")
    console.print("[dim]with @register_parser('.ext') and subclass BaseParser.[/dim]")


# ---------------------------------------------------------------------------
# cmd_setup
# ---------------------------------------------------------------------------


def cmd_setup(args) -> None:
    """Generate integration files for AI tools."""
    import shutil

    root = Path(os.path.abspath(args.root))
    target = args.target

    console.print(LOGO)

    codegraph_cmd = "codegraph"
    if not shutil.which("codegraph"):
        codegraph_cmd = f"{sys.executable} -m codegraph"

    mcp_config = {
        "mcpServers": {
            "codegraph": {
                "command": codegraph_cmd.split()[0],
                "args": codegraph_cmd.split()[1:] + ["serve", "--root", str(root), "--watch", "--reindex"],
            }
        }
    }

    created = []

    if target in ("claude", "all"):
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            import json as _json

            existing = _json.loads(mcp_path.read_text())
            existing.setdefault("mcpServers", {})["codegraph"] = mcp_config["mcpServers"]["codegraph"]
            mcp_path.write_text(_json.dumps(existing, indent=2) + "\n")
        else:
            import json as _json

            mcp_path.write_text(_json.dumps(mcp_config, indent=2) + "\n")
        created.append((".mcp.json", "Claude Code MCP server"))

        # Auto-accept codegraph MCP tools (non-interactive path)
        added = _configure_claude_auto_accept(root)
        if added:
            created.append((".claude/settings.local.json", f"permissions += {', '.join(added)}"))

        # Usage guidelines in CLAUDE.md
        from codegraph.skill_installer import install_usage_guidelines

        path = install_usage_guidelines(root, "claude")
        if path:
            created.append(("CLAUDE.md", "codegraph usage block"))

    from codegraph.skill_installer import install_usage_guidelines as _install_usage

    if target in ("cursor", "all"):
        cursor_dir = root / ".cursor"
        cursor_dir.mkdir(exist_ok=True)
        cursor_mcp = cursor_dir / "mcp.json"
        import json as _json

        cursor_mcp.write_text(_json.dumps(mcp_config, indent=2) + "\n")
        created.append((".cursor/mcp.json", "Cursor MCP server"))
        if _install_usage(root, "cursor"):
            created.append((".cursor/rules/codegraph-usage.mdc", "codegraph usage rule"))

    if target in ("codex", "all"):
        created.append(("(same .mcp.json)", "Codex CLI MCP server"))
        if _install_usage(root, "codex"):
            created.append(("AGENTS.md", "codegraph usage block"))

    if target in ("gemini", "all"):
        created.append(("(same .mcp.json)", "Gemini CLI MCP server"))
        if _install_usage(root, "gemini"):
            created.append(("GEMINI.md", "codegraph usage block"))

    if created:
        panel_lines = [f"  [green]+[/green] {f} [dim]({desc})[/dim]" for f, desc in created]
        console.print(
            Panel(
                "\n".join(panel_lines),
                title=f"[bold green]Setup for {target}[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(f"[dim]Unknown target: {target}[/dim]")
        console.print("[dim]Options: claude, cursor, codex, gemini, all[/dim]")
