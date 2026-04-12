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

    for key in selected_keys:
        _install_integration(root, key)

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

    # -- Step 5: Index now? --
    if total > 0:
        do_index = (
            args.yes
            or questionary.confirm(
                f"Index {total} files now?",
                default=True,
                style=cg_style,
            ).ask()
        )

        if do_index:
            console.print()
            from codegraph.cli.commands_index import cmd_index

            cmd_index(argparse.Namespace(root=str(root), verbose=False))
        else:
            console.print("\n  [dim]Run 'codegraph index' when ready.[/dim]")
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


def _install_integration(root: Path, tool: str) -> None:
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

        # Skills
        _skills_line(".claude/skills/", install_claude(root))

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

    if target in ("cursor", "all"):
        cursor_dir = root / ".cursor"
        cursor_dir.mkdir(exist_ok=True)
        cursor_mcp = cursor_dir / "mcp.json"
        import json as _json

        cursor_mcp.write_text(_json.dumps(mcp_config, indent=2) + "\n")
        created.append((".cursor/mcp.json", "Cursor MCP server"))

    if target in ("codex", "all"):
        created.append(("(same .mcp.json)", "Codex CLI MCP server"))

    if target in ("gemini", "all"):
        created.append(("(same .mcp.json)", "Gemini CLI MCP server"))

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
