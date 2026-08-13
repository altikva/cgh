#!/usr/bin/env python3
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rich-powered CLI for codegraph: thin dispatch layer.

import argparse
import os
import sys
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, VERSION, console
from codegraph.cli.commands_ensurepath import cmd_ensurepath
from codegraph.cli.commands_federate import cmd_federate
from codegraph.cli.commands_findings import cmd_findings
from codegraph.cli.commands_githooks import cmd_githooks
from codegraph.cli.commands_graph import cmd_add_dir, cmd_graph, register_graph_parser
from codegraph.cli.commands_guard import (
    cmd_guard,
    cmd_hook_guard,
    cmd_hook_guard_codex,
)

# ---------------------------------------------------------------------------
# Commands (imported from cli subpackage)
# ---------------------------------------------------------------------------
from codegraph.cli.commands_hooks import cmd_hook_precheck_grep, cmd_hook_precheck_read
from codegraph.cli.commands_impact import cmd_impact
from codegraph.cli.commands_index import (
    cmd_force_index,
    cmd_index,
    cmd_memory_index,
    cmd_plan_index,
    cmd_reindex_hook,
    cmd_serve,
    cmd_watch,
)
from codegraph.cli.commands_init import cmd_init, cmd_parsers, cmd_setup
from codegraph.cli.commands_migrate import cmd_migrate_to_duckdb
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
from codegraph.cli.commands_plugins import cmd_plugins
from codegraph.cli.commands_query import (
    cmd_callees,
    cmd_callers,
    cmd_grep,
    cmd_lookup,
    cmd_outline,
    cmd_search,
)
from codegraph.cli.commands_session import (
    cmd_hook_checkpoint,
    cmd_hook_resume_header,
    cmd_memory,
)
from codegraph.cli.output import add_out_option
from codegraph.core.db import KuzuNotInstalled


def _cmd_serve_owner(args: argparse.Namespace) -> None:
    """Internal: run the HTTP-backed owner process (spawned by cgh serve)."""
    from codegraph.server import owner_main

    owner_main(root=args.root, watch=args.watch, reindex=args.reindex)


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------


def _print_help():
    """Print a beautiful help screen when no command is given."""
    console.print(LOGO)
    console.print(
        f"  [dim]v{VERSION}[/dim]  [dim]---[/dim]  Local code graph index for AI coding assistants\n"
    )

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
                (
                    "graph",
                    "Visualize the graph in browser (imports/calls/classes/docs)",
                ),
            ],
        ),
        (
            "Monitor",
            [
                ("stats", "Graph nodes, edges, call stats, storage"),
                ("status", "Owner / workers state, scan freshness (--workers)"),
                ("logs", "View MCP tool call history"),
                ("history", "Recent indexing activity grouped by day"),
                ("diff", "Files changed since last index"),
                ("impact", "CI: blast radius + tests for a PR diff (JSON/md)"),
                ("parsers", "List registered language parsers"),
                ("findings", "Scanner findings: pii, secrets, summaries"),
                ("files", "List indexed files, or check one (--check)"),
            ],
        ),
        (
            "Maintenance",
            [
                ("doctor", "Health check: verify all components are working"),
                ("compact", "Vacuum SQLite DBs and reclaim space"),
                ("hooks", "Install git hooks that reindex after pull/merge/checkout"),
                ("ensurepath", "Add the cgh command to your PATH"),
                (
                    "migrate-to-duckdb",
                    "Re-index Kuzu repos onto DuckDB (faster + smaller)",
                ),
            ],
        ),
        (
            "Advanced",
            [
                ("watch", "Index + live-watch for file changes"),
                ("add-dir", "Manage extra directories in the graph"),
                (
                    "federate",
                    "Federate sub-repos (parent queries their indexes read-only)",
                ),
                (
                    "force-index",
                    "Index files bypassing .gitignore (requires confirmation)",
                ),
                ("plugins", "List installed cgh plugins and their status"),
                ("guard", "Confidentiality guard: agent-side enforcement"),
                ("examples", "List / install bundled examples (no git needed)"),
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

    console.print(
        "  [bold]Usage:[/bold]  cgh [cyan]<command>[/cyan] [dim][options][/dim]"
    )
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


class _LogoArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that prints the LOGO before any error message."""

    def error(self, message: str) -> None:  # type: ignore[override]
        console.print(LOGO)
        console.print(f"[red]error:[/red] {message}\n")
        console.print(
            "[dim]Run[/dim] [cyan]cgh --help[/cyan] [dim]for the full list of commands.[/dim]"
        )
        sys.exit(2)


def _add_root(p) -> None:
    """Attach the standard --root flag (default: cwd). Every subcommand
    takes one; main() then resolves it up to the nearest .codegraph/."""
    p.add_argument("--root", default=os.getcwd())


def _register_setup_and_serve(sub) -> None:
    """Register init, parsers, setup, index, watch, serve and the hidden precheck entry points."""
    # --- init ---
    p = sub.add_parser(
        "init", help="Initialize codegraph in current directory (interactive wizard)"
    )
    _add_root(p)
    p.add_argument(
        "--yes", "-y", action="store_true", help="Accept all defaults (non-interactive)"
    )
    p.add_argument(
        "--no-children",
        action="store_true",
        help="Don't initialize / refresh federated subrepos",
    )
    p.add_argument(
        "--secure",
        action="store_true",
        help='Enable secure mode (mode = "secure") without prompting',
    )

    # --- parsers ---
    sub.add_parser("parsers", help="List registered parsers and supported languages")

    # --- setup ---
    p = sub.add_parser("setup", help="Generate integration files for AI tools")
    p.add_argument(
        "target",
        choices=["claude", "cursor", "codex", "gemini", "bob", "all"],
        help="Which AI tool to configure",
    )
    _add_root(p)

    # --- index ---
    p = sub.add_parser("index", help="Full index / re-index the repository")
    p.add_argument("--verbose", "-v", action="store_true")
    _add_root(p)
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
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass the running owner and grab the Kuzu write lock directly. "
            "Fails with a clear error if another cgh process holds it. "
            "Default behavior routes through the owner via MCP when one is alive."
        ),
    )

    # --- watch ---
    p = sub.add_parser("watch", help="Index then watch for file changes")
    p.add_argument("--verbose", "-v", action="store_true")
    _add_root(p)

    # --- serve ---
    p = sub.add_parser(
        "serve", help="Start MCP server (stdio proxy to shared HTTP owner)"
    )
    _add_root(p)
    p.add_argument("--watch", action="store_true", help="Enable live file watcher")
    p.add_argument("--reindex", action="store_true", help="Re-index before serving")
    p.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="Spawn owner in background and exit (keeps graph alive for Claude sessions)",
    )
    p.add_argument("--stop", action="store_true", help="Stop a running owner process")

    # --- _serve_owner (hidden internal subcommand) ---
    p = sub.add_parser("_serve_owner", help=argparse.SUPPRESS)
    _add_root(p)
    p.add_argument("--watch", action="store_true")
    p.add_argument("--reindex", action="store_true")

    # --- _hook_precheck_grep / _hook_precheck_read (hidden hook entry points) ---
    # Both read the PreToolUse payload on stdin; no flags.
    sub.add_parser("_hook_precheck_grep", help=argparse.SUPPRESS)
    sub.add_parser("_hook_precheck_read", help=argparse.SUPPRESS)


def _register_inspect(sub) -> None:
    """Register stats, migrate-to-duckdb, logs, search, lookup, callers, callees, outline, doctor."""
    # --- stats ---
    p = sub.add_parser("stats", help="Show graph, edges, call stats, storage")
    _add_root(p)
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument(
        "--live", action="store_true", help="Refresh stats every 500ms (Ctrl-C to stop)"
    )

    p = sub.add_parser("tail", help="Live view of scan/watcher activity")
    _add_root(p)
    p.add_argument(
        "--limit",
        "-n",
        type=int,
        default=30,
        help="Number of recent entries (default: 30)",
    )
    p.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Follow new activity (Ctrl-C to stop)",
    )

    p = sub.add_parser(
        "reset", help="Nuke graph + FTS DBs, kill owner, re-index from scratch"
    )
    _add_root(p)
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p.add_argument(
        "--drop-extra-dirs",
        action="store_true",
        help="Also remove extra_dirs from config.toml",
    )
    p.add_argument(
        "--no-reindex", action="store_true", help="Don't re-index after cleaning"
    )

    # --- migrate-to-duckdb ---
    p = sub.add_parser(
        "migrate-to-duckdb",
        help="Re-index a Kuzu-backed repo into DuckDB, verify counts match, optionally delete graph.db",
    )
    _add_root(p)
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the 'delete graph.db?' confirmation",
    )
    p.add_argument(
        "--keep-kuzu",
        action="store_true",
        help="Always keep graph.db even after a clean migration",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing graph.duckdb (default: abort if present)",
    )

    p = sub.add_parser(
        "status", help="Owner state, scan freshness, counts, extra_dirs in one glance"
    )
    _add_root(p)
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument(
        "--workers",
        action="store_true",
        help="Also list every worker pid + tty + start time",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Before showing status, call incremental_reindex via the owner so the "
            "recorded scan SHA advances to HEAD when every file's blob matches. "
            "Use after the watcher has caught up to a `git pull` / commit burst."
        ),
    )

    p = sub.add_parser(
        "memory-index", help="Scan the Claude Code memory directory into the FTS index"
    )
    _add_root(p)
    p.add_argument("--verbose", "-v", action="store_true")

    p = sub.add_parser("plan-index", help="Scan ~/.claude/plans/ into the FTS index")
    _add_root(p)
    p.add_argument("--verbose", "-v", action="store_true")

    # --- logs ---
    p = sub.add_parser("logs", help="View MCP tool call logs")
    _add_root(p)
    p.add_argument("--tool", "-t", help="Filter by tool name")
    p.add_argument("--errors", "-e", action="store_true", help="Show only errors")
    p.add_argument(
        "--limit", "-n", type=int, default=50, help="Max entries (default: 50)"
    )
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--clear", action="store_true", help="Clear all logs")

    # --- search ---
    p = sub.add_parser(
        "grep",
        help="Regex/substring search across indexed files (ripgrep under the hood)",
    )
    p.add_argument("pattern", help="regex (default) or literal (with --fixed)")
    p.add_argument("--glob", "-g", default="", help="shell glob filter, e.g. '*.py'")
    p.add_argument("--limit", "-n", type=int, default=50)
    p.add_argument(
        "--fixed", "-F", action="store_true", help="literal substring, not regex"
    )
    p.add_argument("--case", "-s", action="store_true", help="case-sensitive match")
    p.add_argument("--json", action="store_true")
    _add_root(p)

    p = sub.add_parser("search", help="Search symbols by name (fuzzy)")
    p.add_argument("query", help="Search query")
    _add_root(p)
    p.add_argument(
        "--limit", "-n", type=int, default=100, help="Page size (default: 100)"
    )
    p.add_argument(
        "--offset",
        "-o",
        type=int,
        default=0,
        help="Skip first N results (for pagination)",
    )
    p.add_argument("--json", action="store_true")

    # --- lookup ---
    p = sub.add_parser("lookup", help="Find where a symbol is defined")
    p.add_argument("name", help="Symbol name")
    _add_root(p)

    # --- callers ---
    p = sub.add_parser("callers", help="Find all callers of a function (tree view)")
    p.add_argument("fn_name", help="Function name")
    _add_root(p)

    # --- callees ---
    p = sub.add_parser(
        "callees", help="Find all functions called by a function (tree view)"
    )
    p.add_argument("fn_name", help="Function name")
    _add_root(p)

    # --- outline ---
    p = sub.add_parser("outline", help="Show heading outline of a Markdown file (tree)")
    p.add_argument("file", help="Markdown file path")
    _add_root(p)

    # --- doctor ---
    p = sub.add_parser("doctor", help="Health check: verify all codegraph components")
    _add_root(p)


def _register_analysis(sub) -> None:
    """Register diff, impact, history, compact, graph, add-dir, federate, force-index, hooks."""
    # --- diff ---
    p = sub.add_parser("diff", help="Show files changed since last index")
    _add_root(p)
    p.add_argument(
        "--since", default="HEAD", help="Git ref to diff against (default: HEAD)"
    )

    # --- impact (CI mode: blast radius + tests for a PR diff) ---
    p = sub.add_parser(
        "impact",
        help="CI: blast radius + tests for files changed since a git ref",
    )
    _add_root(p)
    p.add_argument(
        "--since",
        default="HEAD~1",
        help="Git ref to diff the working tree against (default: HEAD~1)",
    )
    p.add_argument(
        "--json", action="store_true", help="Emit JSON (shorthand for --format json)"
    )
    p.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="Output format: md (PR comment) or json (default: md). "
        "The graph index should be fresh: run `cgh index` first in CI.",
    )
    add_out_option(p, what="the report")

    # --- history ---
    p = sub.add_parser("history", help="Show recent indexing activity by day")
    _add_root(p)
    p.add_argument(
        "--days", "-d", type=int, default=7, help="Number of days to show (default: 7)"
    )

    # --- compact ---
    p = sub.add_parser("compact", help="Vacuum SQLite DBs and show before/after sizes")
    _add_root(p)

    # --- graph + add-dir ---
    register_graph_parser(sub)

    # --- fetch (URL into the searchable index) ---
    from codegraph.cli.commands_fetch import register_fetch_parser

    register_fetch_parser(sub)

    # --- files (list indexed files / check one) ---
    from codegraph.cli.commands_files import register_files_parser

    register_files_parser(sub)

    # --- examples (bundled, installable without git/network) ---
    from codegraph.cli.commands_examples import register_examples_parser

    register_examples_parser(sub)

    # --- federate ---
    p = sub.add_parser(
        "federate",
        help="Manage federated subrepos (parent queries their indexes read-only)",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=["add", "remove", "list", "verify", "up", "down"],
        default="list",
        help="Action (default: list)",
    )
    p.add_argument("paths", nargs="*", help="Subrepo paths (for add / remove)")
    _add_root(p)

    # --- force-index ---
    p = sub.add_parser(
        "force-index", help="Force-index files/dirs (bypasses .gitignore)"
    )
    p.add_argument("paths", nargs="+", help="Files or directories")
    _add_root(p)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # --- hooks ---
    p = sub.add_parser(
        "hooks",
        help="Manage git hooks that refresh the graph after pull/merge/checkout/rebase",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=["install", "uninstall", "status"],
        default="status",
        help="Action (default: status)",
    )
    p.add_argument(
        "--shared",
        action="store_true",
        help="Allow install into a shared core.hooksPath (affects every repo)",
    )
    _add_root(p)


def _register_state_and_hooks(sub) -> None:
    """Register ensurepath, the git and agent hook entry points, plugins, guard, session continuity, findings."""
    # --- ensurepath ---
    p = sub.add_parser(
        "ensurepath", help="Add the cgh command's directory to your PATH"
    )
    p.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt"
    )

    # --- _reindex_hook (internal: invoked by the git hooks) ---
    p = sub.add_parser("_reindex_hook")
    _add_root(p)

    # --- plugins ---
    p = sub.add_parser("plugins", help="List installed cgh plugins and their status")
    _add_root(p)
    p.add_argument("--json", action="store_true")

    # --- guard ---
    p = sub.add_parser("guard", help="Confidentiality guard: agent-side enforcement")
    p.add_argument("action", nargs="?", default="status", choices=["status", "sync"])
    _add_root(p)

    # --- _hook_guard (internal: invoked by agent pre-tool-use hooks) ---
    sub.add_parser("_hook_guard", help=argparse.SUPPRESS)
    sub.add_parser("_hook_guard_codex", help=argparse.SUPPRESS)

    # --- session continuity (lifecycle hooks + memory hygiene) ---
    sub.add_parser("_hook_checkpoint", help=argparse.SUPPRESS)
    sub.add_parser("_hook_resume_header", help=argparse.SUPPRESS)
    p = sub.add_parser("memory", help="Shared memory hygiene (review stale entries)")
    p.add_argument("action", nargs="?", default="review", choices=["review"])
    p.add_argument("--days", type=int, default=90)
    _add_root(p)

    # --- findings ---
    p = sub.add_parser("findings", help="Query scanner findings (pii, secrets, ...)")
    _add_root(p)
    p.add_argument("file", nargs="?", default="", help="Restrict to one file")
    p.add_argument("--key", default="", help="Key prefix filter, e.g. pii. or secret")
    p.add_argument("--severity", default="", choices=["", "info", "warn", "block"])
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")


def main() -> None:
    # Strip trailing CR/LF from every argument. On Windows a wrapper script
    # or config saved with CRLF line endings can pass a token like "serve\r",
    # which argparse then rejects as an invalid choice. No real argument ends
    # in a carriage return or newline, so this is safe.
    sys.argv[:] = [a.rstrip("\r\n") for a in sys.argv]

    # Show pretty help if no args
    if len(sys.argv) <= 1 or sys.argv[1] in ("-h", "--help", "help"):
        _print_help()
        return
    if sys.argv[1] in ("--version", "-V"):
        console.print(LOGO)
        console.print(f"  [bold cyan]codegraph[/bold cyan] {VERSION}")
        return

    ap = _LogoArgumentParser(prog="codegraph", add_help=False)
    _add_root(ap)
    ap.add_argument("--version", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    sub = ap.add_subparsers(dest="cmd", parser_class=_LogoArgumentParser)

    _register_setup_and_serve(sub)
    _register_inspect(sub)
    _register_analysis(sub)
    _register_state_and_hooks(sub)

    # Load installed plugins BEFORE parse_args so their parsers register
    # and their CLI verbs exist. The repo root isn't parsed yet, so config
    # resolution walks up from the CWD; a failure here must never take the
    # CLI down (the plugin is reported broken by `cgh plugins` instead).
    try:
        from codegraph.core.config import find_codegraph_root as _find_root
        from codegraph.plugins import cli_registrars, load_plugins

        load_plugins(_find_root(os.getcwd()))
        for _plugin_name, _registrar in cli_registrars():
            try:
                _registrar(sub)
            except Exception as exc:
                print(
                    f"[codegraph] plugin {_plugin_name}: CLI registration failed: {exc}",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"[codegraph] plugin loading failed: {exc}", file=sys.stderr)

    args = ap.parse_args()

    if args.help or not args.cmd:
        _print_help()
        return

    # Resolve the codegraph root by walking up to the nearest .codegraph/, the
    # way git finds its repo root via .git. This lets every command work from
    # a subdirectory of an initialized repo. init/setup create in the literal
    # directory, and _serve_owner / _reindex_hook get an explicit root from
    # their spawner, so those opt out. The hint goes to stderr to keep stdout
    # clean for --json output and piping.
    _NO_ROOT_WALK = {"init", "setup", "_serve_owner", "_reindex_hook"}
    if args.cmd not in _NO_ROOT_WALK and getattr(args, "root", None):
        from codegraph.core.config import find_codegraph_root

        discovered = find_codegraph_root(args.root)
        if discovered is not None and discovered != Path(args.root).resolve():
            from rich.console import Console as _Console

            _Console(stderr=True).print(
                f"[dim]Using codegraph root: {discovered}[/dim]"
            )
            args.root = str(discovered)

    dispatch = {
        "init": cmd_init,
        "setup": cmd_setup,
        "parsers": cmd_parsers,
        "index": cmd_index,
        "watch": cmd_watch,
        "serve": cmd_serve,
        "_serve_owner": _cmd_serve_owner,
        "_hook_precheck_grep": cmd_hook_precheck_grep,
        "_hook_precheck_read": cmd_hook_precheck_read,
        "migrate-to-duckdb": cmd_migrate_to_duckdb,
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
        "impact": cmd_impact,
        "history": cmd_history,
        "compact": cmd_compact,
        "graph": cmd_graph,
        "add-dir": cmd_add_dir,
        "federate": cmd_federate,
        "force-index": cmd_force_index,
        "hooks": cmd_githooks,
        "ensurepath": cmd_ensurepath,
        "_reindex_hook": cmd_reindex_hook,
        "plugins": cmd_plugins,
        "findings": cmd_findings,
        "guard": cmd_guard,
        "_hook_guard": cmd_hook_guard,
        "_hook_guard_codex": cmd_hook_guard_codex,
        "_hook_checkpoint": cmd_hook_checkpoint,
        "_hook_resume_header": cmd_hook_resume_header,
        "memory": cmd_memory,
    }

    # Plugin-registered verbs dispatch through argparse's set_defaults(func=…)
    handler = dispatch.get(args.cmd) or getattr(args, "func", None)
    if not handler:
        _print_help()
        return

    try:
        handler(args)
    except KuzuNotInstalled as exc:
        # Known, recoverable situation (Kuzu repo + kuzu not installed).
        # Print the message and how to fix it, not a traceback. Pass
        # --verbose to see the full stack.
        if getattr(args, "verbose", False):
            raise
        # Render the message as literal text. It contains `cgh[kuzu]`,
        # which Rich would otherwise parse as markup and drop.
        from rich.text import Text

        console.print(
            Panel(
                Text(str(exc)),
                title="[red]Kuzu backend not available[/red]",
                border_style="red",
            )
        )
        console.print("[dim]Run with --verbose to see the full traceback.[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
