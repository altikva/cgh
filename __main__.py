#!/usr/bin/env python3
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rich-powered CLI for codegraph — thin dispatch layer.

import argparse
import os
import sys

from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, VERSION, console
from codegraph.cli.commands_graph import cmd_add_dir, cmd_graph, register_graph_parser
from codegraph.cli.commands_index import (
    cmd_force_index,
    cmd_index,
    cmd_memory_index,
    cmd_plan_index,
    cmd_serve,
    cmd_watch,
)

# ---------------------------------------------------------------------------
# Commands (imported from cli subpackage)
# ---------------------------------------------------------------------------
from codegraph.cli.commands_init import cmd_init, cmd_parsers, cmd_setup
from codegraph.cli.commands_monitor import (
    cmd_compact,
    cmd_diff,
    cmd_doctor,
    cmd_history,
    cmd_logs,
    cmd_reset,
    cmd_stats,
    cmd_status,
    cmd_tail,
)
from codegraph.cli.commands_query import (
    cmd_callees,
    cmd_callers,
    cmd_grep,
    cmd_lookup,
    cmd_outline,
    cmd_search,
)


def _cmd_serve_owner(args) -> None:
    """Internal: run the HTTP-backed owner process (spawned by cgh serve)."""
    from codegraph.server import owner_main

    owner_main(root=args.root, watch=args.watch, reindex=args.reindex)


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------


def _print_help():
    """Print a beautiful help screen when no command is given."""
    console.print(LOGO)
    console.print(f"  [dim]v{VERSION}[/dim]  [dim]---[/dim]  Local code graph index for AI coding assistants\n")

    sections = [
        (
            "Getting Started",
            [
                ("init", "Initialize codegraph in any project (interactive wizard)"),
                ("index", "Build / rebuild the code graph"),
                ("serve", "Start MCP server (for Claude, Cursor, Codex, Gemini)"),
                ("setup", "Configure integration for a specific AI tool"),
            ],
        ),
        (
            "Query",
            [
                ("search", "Fuzzy search symbols by name"),
                ("lookup", "Find exact symbol definition"),
                ("callers", "Who calls this function? (tree view)"),
                ("callees", "What does this function call? (tree view)"),
                ("outline", "Heading tree of a Markdown file"),
                ("graph", "Visualize the graph in browser (imports/calls/classes/docs)"),
            ],
        ),
        (
            "Monitor",
            [
                ("stats", "Graph nodes, edges, call stats, storage"),
                ("logs", "View MCP tool call history"),
                ("history", "Recent indexing activity grouped by day"),
                ("diff", "Files changed since last index"),
                ("parsers", "List registered language parsers"),
            ],
        ),
        (
            "Maintenance",
            [
                ("doctor", "Health check --- verify all components are working"),
                ("compact", "Vacuum SQLite DBs and reclaim space"),
            ],
        ),
        (
            "Advanced",
            [
                ("watch", "Index + live-watch for file changes"),
                ("add-dir", "Manage extra directories in the graph"),
                ("force-index", "Index files bypassing .gitignore (requires confirmation)"),
            ],
        ),
    ]

    for section_name, commands in sections:
        table = Table(
            box=None,
            show_header=False,
            padding=(0, 2),
            title=f"  [bold]{section_name}[/bold]",
            title_justify="left",
            title_style="",
        )
        table.add_column(width=15, style="cyan bold")
        table.add_column(style="dim")
        for cmd, desc in commands:
            table.add_row(cmd, desc)
        console.print(table)
        console.print()

    console.print("  [bold]Usage:[/bold]  cgh [cyan]<command>[/cyan] [dim][options][/dim]")
    console.print("  [bold]Help:[/bold]   cgh [cyan]<command>[/cyan] --help")
    console.print()

    console.print(
        Panel(
            "[cyan]cgh init[/cyan]                       [dim]Setup in any project[/dim]\n"
            '[cyan]cgh search[/cyan] [white]"Handler"[/white]           [dim]Find symbols[/dim]\n'
            "[cyan]cgh callers[/cyan] [white]verify_token[/white]       [dim]Call graph (tree)[/dim]\n"
            "[cyan]cgh outline[/cyan] [white]README.md[/white]          [dim]Doc structure (tree)[/dim]\n"
            "[cyan]cgh stats[/cyan]                       [dim]Full statistics[/dim]\n"
            "[cyan]cgh graph[/cyan] [white]calls[/white] -s verify    [dim]Call graph in browser[/dim]\n"
            "[cyan]cgh add-dir[/cyan] [white]add ../frontend[/white]  [dim]Multi-repo graph[/dim]\n"
            "[cyan]cgh doctor[/cyan]                      [dim]Health check[/dim]\n"
            "[cyan]cgh serve[/cyan] --watch --reindex     [dim]MCP server[/dim]",
            title="[bold]Examples[/bold]",
            border_style="dim",
            padding=(1, 3),
        )
    )


# ---------------------------------------------------------------------------
# Argument parser + dispatch
# ---------------------------------------------------------------------------


def main() -> None:
    # Show pretty help if no args
    if len(sys.argv) <= 1 or sys.argv[1] in ("-h", "--help", "help"):
        _print_help()
        return
    if sys.argv[1] in ("--version", "-V"):
        console.print(f"[bold cyan]codegraph[/bold cyan] {VERSION}")
        return

    class _LogoArgumentParser(argparse.ArgumentParser):
        """ArgumentParser that prints the LOGO before any error message."""

        def error(self, message: str) -> None:  # type: ignore[override]
            console.print(LOGO)
            console.print(f"[red]error:[/red] {message}\n")
            console.print("[dim]Run[/dim] [cyan]cgh --help[/cyan] [dim]for the full list of commands.[/dim]")
            sys.exit(2)

    ap = _LogoArgumentParser(prog="codegraph", add_help=False)
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--version", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    sub = ap.add_subparsers(dest="cmd", parser_class=_LogoArgumentParser)

    # --- init ---
    p = sub.add_parser("init", help="Initialize codegraph in current directory (interactive wizard)")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--yes", "-y", action="store_true", help="Accept all defaults (non-interactive)")

    # --- parsers ---
    sub.add_parser("parsers", help="List registered parsers and supported languages")

    # --- setup ---
    p = sub.add_parser("setup", help="Generate integration files for AI tools")
    p.add_argument("target", choices=["claude", "cursor", "codex", "gemini", "all"], help="Which AI tool to configure")
    p.add_argument("--root", default=os.getcwd())

    # --- index ---
    p = sub.add_parser("index", help="Full index / re-index the repository")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument(
        "--method",
        "-m",
        choices=["auto", "git_ls_files", "os_walk", "find", "git_diff", "incremental"],
        default="auto",
        help=(
            "File discovery strategy. auto (default) = git_ls_files with os_walk "
            "fallback. incremental = only drifted blob SHAs. git_diff = only "
            "files changed since last scan."
        ),
    )

    # --- watch ---
    p = sub.add_parser("watch", help="Index then watch for file changes")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--root", default=os.getcwd())

    # --- serve ---
    p = sub.add_parser("serve", help="Start MCP server (stdio proxy to shared HTTP owner)")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--watch", action="store_true", help="Enable live file watcher")
    p.add_argument("--reindex", action="store_true", help="Re-index before serving")

    # --- _serve_owner (hidden internal subcommand) ---
    p = sub.add_parser("_serve_owner", help=argparse.SUPPRESS)
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--watch", action="store_true")
    p.add_argument("--reindex", action="store_true")

    # --- stats ---
    p = sub.add_parser("stats", help="Show graph, edges, call stats, storage")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--live", action="store_true", help="Refresh stats every 500ms (Ctrl-C to stop)")

    p = sub.add_parser("tail", help="Live view of scan/watcher activity")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--limit", "-n", type=int, default=30, help="Number of recent entries (default: 30)")
    p.add_argument("--follow", "-f", action="store_true", help="Follow new activity (Ctrl-C to stop)")

    p = sub.add_parser("reset", help="Nuke graph + FTS DBs, kill owner, re-index from scratch")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p.add_argument("--drop-extra-dirs", action="store_true", help="Also remove extra_dirs from config.toml")
    p.add_argument("--no-reindex", action="store_true", help="Don't re-index after cleaning")

    p = sub.add_parser("status", help="Owner state, scan freshness, counts, extra_dirs in one glance")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--workers", action="store_true", help="Also list every worker pid + tty + start time")

    p = sub.add_parser("memory-index", help="Scan the Claude Code memory directory into the FTS index")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--verbose", "-v", action="store_true")

    p = sub.add_parser("plan-index", help="Scan ~/.claude/plans/ into the FTS index")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--verbose", "-v", action="store_true")

    # --- logs ---
    p = sub.add_parser("logs", help="View MCP tool call logs")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--tool", "-t", help="Filter by tool name")
    p.add_argument("--errors", "-e", action="store_true", help="Show only errors")
    p.add_argument("--limit", "-n", type=int, default=50, help="Max entries (default: 50)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--clear", action="store_true", help="Clear all logs")

    # --- search ---
    p = sub.add_parser("grep", help="Regex/substring search across indexed files (ripgrep under the hood)")
    p.add_argument("pattern", help="regex (default) or literal (with --fixed)")
    p.add_argument("--glob", "-g", default="", help="shell glob filter, e.g. '*.py'")
    p.add_argument("--limit", "-n", type=int, default=50)
    p.add_argument("--fixed", "-F", action="store_true", help="literal substring, not regex")
    p.add_argument("--case", "-s", action="store_true", help="case-sensitive match")
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=os.getcwd())

    p = sub.add_parser("search", help="Search symbols by name (fuzzy)")
    p.add_argument("query", help="Search query")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--limit", "-n", type=int, default=100, help="Page size (default: 100)")
    p.add_argument("--offset", "-o", type=int, default=0, help="Skip first N results (for pagination)")
    p.add_argument("--json", action="store_true")

    # --- lookup ---
    p = sub.add_parser("lookup", help="Find where a symbol is defined")
    p.add_argument("name", help="Symbol name")
    p.add_argument("--root", default=os.getcwd())

    # --- callers ---
    p = sub.add_parser("callers", help="Find all callers of a function (tree view)")
    p.add_argument("fn_name", help="Function name")
    p.add_argument("--root", default=os.getcwd())

    # --- callees ---
    p = sub.add_parser("callees", help="Find all functions called by a function (tree view)")
    p.add_argument("fn_name", help="Function name")
    p.add_argument("--root", default=os.getcwd())

    # --- outline ---
    p = sub.add_parser("outline", help="Show heading outline of a Markdown file (tree)")
    p.add_argument("file", help="Markdown file path")
    p.add_argument("--root", default=os.getcwd())

    # --- doctor ---
    p = sub.add_parser("doctor", help="Health check --- verify all codegraph components")
    p.add_argument("--root", default=os.getcwd())

    # --- diff ---
    p = sub.add_parser("diff", help="Show files changed since last index")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--since", default="HEAD", help="Git ref to diff against (default: HEAD)")

    # --- history ---
    p = sub.add_parser("history", help="Show recent indexing activity by day")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--days", "-d", type=int, default=7, help="Number of days to show (default: 7)")

    # --- compact ---
    p = sub.add_parser("compact", help="Vacuum SQLite DBs and show before/after sizes")
    p.add_argument("--root", default=os.getcwd())

    # --- graph + add-dir ---
    register_graph_parser(sub)

    # --- force-index ---
    p = sub.add_parser("force-index", help="Force-index files/dirs (bypasses .gitignore)")
    p.add_argument("paths", nargs="+", help="Files or directories")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    args = ap.parse_args()

    if args.help or not args.cmd:
        _print_help()
        return

    dispatch = {
        "init": cmd_init,
        "setup": cmd_setup,
        "parsers": cmd_parsers,
        "index": cmd_index,
        "watch": cmd_watch,
        "serve": cmd_serve,
        "_serve_owner": _cmd_serve_owner,
        "stats": cmd_stats,
        "status": cmd_status,
        "tail": cmd_tail,
        "reset": cmd_reset,
        "memory-index": cmd_memory_index,
        "plan-index": cmd_plan_index,
        "logs": cmd_logs,
        "grep": cmd_grep,
        "search": cmd_search,
        "lookup": cmd_lookup,
        "callers": cmd_callers,
        "callees": cmd_callees,
        "outline": cmd_outline,
        "doctor": cmd_doctor,
        "diff": cmd_diff,
        "history": cmd_history,
        "compact": cmd_compact,
        "graph": cmd_graph,
        "add-dir": cmd_add_dir,
        "force-index": cmd_force_index,
    }

    handler = dispatch.get(args.cmd)
    if handler:
        handler(args)
    else:
        _print_help()


if __name__ == "__main__":
    main()
