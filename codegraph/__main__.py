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
from codegraph.cli.commands_artifact import cmd_artifact, register_artifact_parser
from codegraph.cli.commands_backend import cmd_backend, register_backend_parser
from codegraph.cli.commands_bob import (
    cmd_bob_gate,
    cmd_bob_prompt,
    cmd_bob_session_start,
    cmd_bob_stop,
    cmd_bob_tool_log,
)
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
from codegraph.cli.commands_hooks import (
    cmd_hook_precheck_bash,
    cmd_hook_precheck_grep,
    cmd_hook_precheck_read,
)
from codegraph.cli.commands_impact import cmd_impact
from codegraph.cli.commands_index import (
    cmd_force_index,
    cmd_index,
    cmd_memory_index,
    cmd_plan_index,
    cmd_reindex_hook,
    cmd_serve,
    cmd_stop,
    cmd_watch,
)
from codegraph.cli.commands_init import cmd_init, cmd_parsers, cmd_setup
from codegraph.cli.commands_knowledge import cmd_knowledge, register_knowledge_parser
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
from codegraph.cli.commands_papercut import cmd_papercut, register_papercut_parser
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
                ("stop", "Stop this repo's owner + worker (alias of serve --stop)"),
                ("setup", "Configure integration for a specific AI tool"),
            ],
        ),
        (
            "Query",
            [
                ("search", "Fuzzy search symbols by name; --text for free-text prose"),
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
                ("backend", "Show or switch the graph backend (sqlite/duckdb)"),
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
                (
                    "guard",
                    "Deprecated: cleans up what older cgh wrote (--remove-rules)",
                ),
                ("papercut", "Read this repo's papercuts (agents log via knowledge)"),
                ("artifact", "Recall/record summaries of files cgh can't parse"),
                ("knowledge", "Promote a worktree's learnings to its main checkout"),
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

    # A table, like the command sections above: the description column was
    # aligned by hand with literal spaces, and four of the nine rows were off
    # by one or two, which shows up in the landing screen and in the README
    # capture of it. Rich measures the rendered width, so it cannot drift.
    examples = Table(box=None, show_header=False, padding=(0, 2))
    examples.add_column()
    examples.add_column(style="dim")
    for command, purpose in (
        ("[cyan]cgh init[/cyan]", "Setup in any project"),
        ('[cyan]cgh search[/cyan] [white]"Handler"[/white]', "Find symbols"),
        ("[cyan]cgh callers[/cyan] [white]verify_token[/white]", "Call graph (tree)"),
        ("[cyan]cgh outline[/cyan] [white]README.md[/white]", "Doc structure (tree)"),
        ("[cyan]cgh stats[/cyan]", "Full statistics"),
        (
            "[cyan]cgh graph[/cyan] [white]calls[/white] -s verify",
            "Call graph in browser",
        ),
        ("[cyan]cgh add-dir[/cyan] [white]add ../frontend[/white]", "Multi-repo graph"),
        ("[cyan]cgh doctor[/cyan]", "Health check"),
        ("[cyan]cgh serve[/cyan] --watch --reindex", "MCP server"),
    ):
        examples.add_row(command, purpose)

    console.print(
        Panel(
            examples,
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
        "--from",
        dest="from_",
        default="",
        metavar="CHECKOUT",
        help="Seed the index and knowledge from another checkout of this repo "
        "(its owner must be stopped), then reindex only the files that differ. "
        "Skips a full index on a fresh worktree.",
    )
    p.add_argument(
        "--no-children",
        action="store_true",
        help="Don't initialize / refresh federated subrepos",
    )
    # Secure mode was removed in 0.15.0; the flag stays accepted so old
    # scripts do not break, and only prints a deprecation note.
    p.add_argument("--secure", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--tools",
        default="",
        help="Comma-separated agent tools to wire regardless of detection "
        "(claude,cursor,codex,gemini,bob). For a fresh repo where cgh cannot "
        "detect the tool yet.",
    )
    p.add_argument(
        "--no-hide-footprint",
        dest="hide_footprint",
        action="store_false",
        help="In a git worktree, do NOT keep cgh's own writes (usage block, MCP "
        "wiring, skills, rule) out of git via skip-worktree + info/exclude.",
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
            "Bypass the running owner and grab the graph DB write lock directly. "
            "Fails with a clear error if another cgh process holds it. "
            "Default behavior routes through the owner via MCP when one is alive."
        ),
    )
    p.add_argument(
        "--no-claude-state",
        action="store_true",
        help=(
            "Skip the memory and plan scans that otherwise run with every "
            "index. They read ~/.claude, not the repository."
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

    # --- stop (discoverable alias for `serve --stop`) ---
    p = sub.add_parser("stop", help="Stop this repo's owner and unregister the worker")
    _add_root(p)

    # --- _serve_owner (hidden internal subcommand) ---
    p = sub.add_parser("_serve_owner", help=argparse.SUPPRESS)
    _add_root(p)
    p.add_argument("--watch", action="store_true")
    p.add_argument("--reindex", action="store_true")

    # --- _hook_precheck_{grep,read,bash} (hidden hook entry points) ---
    # All read the PreToolUse payload on stdin; no flags.
    sub.add_parser("_hook_precheck_grep", help=argparse.SUPPRESS)
    sub.add_parser("_hook_precheck_read", help=argparse.SUPPRESS)
    sub.add_parser("_hook_precheck_bash", help=argparse.SUPPRESS)


def _register_inspect(sub) -> None:
    """Register stats, logs, search, lookup, callers, callees, outline, doctor."""
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

    p = sub.add_parser(
        "search", help="Search symbols by name (fuzzy), or free text with --text"
    )
    p.add_argument("query", nargs="?", help="Symbol name query")
    p.add_argument(
        "--text",
        "-t",
        metavar="PROSE",
        help="Free-text search over names, docstrings and Markdown sections: "
        "stopwords (English + French) dropped, any remaining term matches, "
        "ranked by bm25",
    )
    _add_root(p)
    p.add_argument(
        "--limit",
        "-n",
        type=int,
        default=None,
        help="Page size (default: 100, or 20 with --text)",
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
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any blocking check fails (for scripts/CI)",
    )
    p.add_argument(
        "--owner",
        action="store_true",
        help=(
            "Probe only this repo's running owner, for a supervisor: exit 0 "
            "when no owner runs or it answers, 1 when it is alive but stuck. "
            "Never starts an owner"
        ),
    )


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
    register_backend_parser(sub)
    register_papercut_parser(sub)
    register_artifact_parser(sub)
    register_knowledge_parser(sub)

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
    p = sub.add_parser(
        "guard",
        help="Deprecated: cleans up what older cgh wrote (--remove-rules)",
    )
    # The old actions stay accepted so scripts keep working; both clean up.
    p.add_argument("action", nargs="?", default="status", choices=["status", "sync"])
    p.add_argument(
        "--remove-rules",
        action="store_true",
        help="Also remove the Read() deny rules and the .bobignore block an "
        "older cgh wrote (recorded in .codegraph/guard_denies.json). Without "
        "it they are kept, since they still keep files from your agent.",
    )
    _add_root(p)

    # --- _hook_guard (internal: invoked by agent pre-tool-use hooks) ---
    sub.add_parser("_hook_guard", help=argparse.SUPPRESS)
    sub.add_parser("_hook_guard_codex", help=argparse.SUPPRESS)

    # --- session continuity (lifecycle hooks + memory hygiene) ---
    sub.add_parser("_hook_checkpoint", help=argparse.SUPPRESS)
    sub.add_parser("_hook_resume_header", help=argparse.SUPPRESS)
    for _bob_hook in ("session_start", "tool_log", "prompt", "gate", "stop"):
        sub.add_parser(f"_bob_{_bob_hook}", help=argparse.SUPPRESS)
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


_HOOK_COMMAND_PREFIXES = ("_hook_", "_bob_")


def main() -> None:
    # Strip trailing CR/LF from every argument. On Windows a wrapper script
    # or config saved with CRLF line endings can pass a token like "serve\r",
    # which argparse then rejects as an invalid choice. No real argument ends
    # in a carriage return or newline, so this is safe.
    sys.argv[:] = [a.rstrip("\r\n") for a in sys.argv]

    # Hook commands take no arguments, but the commands cgh writes into an
    # agent's settings end with a marker comment (`cgh _bob_prompt  # cgh-bob-
    # prompt`) so a re-init can find its own hooks. A host that runs the
    # command without a POSIX shell passes `#` and the marker through as
    # arguments; argparse then exits 2, and in Bob exit 2 BLOCKS the event
    # (UserPromptSubmit, PreCompact). Drop whatever follows a hook command.
    if len(sys.argv) > 2 and sys.argv[1].startswith(_HOOK_COMMAND_PREFIXES):
        del sys.argv[2:]
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith(_HOOK_COMMAND_PREFIXES) or sys.argv[1] == "_reindex_hook"
    ):
        # Owner calls made from a hook are logged as "hook", not "cli", so
        # usage data separates hook traffic from what agents and people ask.
        from codegraph.state.call_log import ORIGIN_ENV

        os.environ[ORIGIN_ENV] = "hook"
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith(_HOOK_COMMAND_PREFIXES)
        or sys.argv[1] in ("status", "doctor")
    ):
        # Agents parse hook output: the config deprecation notice (legacy
        # mode = "secure", dead keys) must not ride along with a hook, not
        # even on stderr. status and doctor print it in their own output.
        from codegraph.core.config import suppress_legacy_mode_warning

        suppress_legacy_mode_warning()

    # A Windows console or pipe defaults to the ANSI codepage (cp1252), which
    # cannot encode rich's spinner frames or box glyphs: printing one raised
    # UnicodeEncodeError and aborted `cgh init` mid-run (seen under Git Bash,
    # where the progress console is forced on). Degrade the glyph instead.
    for _stream in (sys.stdout, sys.stderr):
        enc = (getattr(_stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")

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
        "stop": cmd_stop,
        "_serve_owner": _cmd_serve_owner,
        "_hook_precheck_grep": cmd_hook_precheck_grep,
        "_hook_precheck_read": cmd_hook_precheck_read,
        "_hook_precheck_bash": cmd_hook_precheck_bash,
        "stats": cmd_stats,
        "status": cmd_status,
        "backend": cmd_backend,
        "papercut": cmd_papercut,
        "artifact": cmd_artifact,
        "knowledge": cmd_knowledge,
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
        "_bob_session_start": cmd_bob_session_start,
        "_bob_tool_log": cmd_bob_tool_log,
        "_bob_prompt": cmd_bob_prompt,
        "_bob_gate": cmd_bob_gate,
        "_bob_stop": cmd_bob_stop,
        "memory": cmd_memory,
    }

    # Plugin-registered verbs dispatch through argparse's set_defaults(func=…)
    handler = dispatch.get(args.cmd) or getattr(args, "func", None)
    if not handler:
        _print_help()
        return

    try:
        handler(args)
    except Exception as exc:
        hint = _outdated_store_hint(args, exc)
        if not hint:
            raise
        print(hint, file=sys.stderr)
        raise SystemExit(1) from None


def _outdated_store_hint(args: argparse.Namespace, exc: Exception) -> str:
    """The remedy when a query hit a store written in an older graph format.

    Upgrading cgh does not touch an existing index until the next index run or
    owner start, and a query opening it read-only in between finds tables
    without the columns this version expects. That is a missing reindex, not
    a bug, so say so instead of printing the database error."""
    text = str(exc)
    schema_error = type(exc).__name__ in {
        "BinderException",
        "CatalogException",
        "OperationalError",
    } and any(k in text for k in ("column", "Column", "table", "Table"))
    if not schema_error:
        return ""
    from codegraph.state.scan_meta import outdated_store_message

    root = os.path.abspath(getattr(args, "root", None) or os.getcwd())
    return outdated_store_message(root)


if __name__ == "__main__":
    main()
