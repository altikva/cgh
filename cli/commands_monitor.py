# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands — stats, logs, history, diff, doctor, compact.

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codegraph.cli import LOGO, _get_conn, _lang_color, _rows, console

# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------


def cmd_stats(args) -> None:
    root = os.path.abspath(args.root)
    conn = _get_conn(root, readonly=True)

    # Graph stats (may be None if DB is locked)
    graph = {}
    edges = {}
    graph_locked = conn is None

    if conn is not None:
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            # Kuzu Cypher requires literal labels — safe: fixed allowlist
            query = "MATCH (n:" + label + ") RETURN count(n) AS c"
            r = conn.execute(query)
            for row in _rows(r):
                graph[label] = row["c"]

        for edge_type in (
            "IMPORTS",
            "DEFINES_FN",
            "DEFINES_CLASS",
            "CALLS",
            "INHERITS",
            "HAS_METHOD",
            "DEFINES_SECTION",
            "MD_REFS_SYMBOL",
            "MD_REFS_CLASS",
            "CONTAINS_SECTION",
        ):
            try:
                query = "MATCH ()-[r:" + edge_type + "]->() RETURN count(r) AS c"
                r = conn.execute(query)
                for row in _rows(r):
                    if row["c"] > 0:
                        edges[edge_type] = row["c"]
            except Exception:
                pass

    # Call logs
    from codegraph.call_log import get_stats

    call_stats = get_stats(root)

    # FTS
    fts_count = 0
    try:
        from codegraph.fts import get_fts_conn

        fts_conn = get_fts_conn(root)
        fts_count = fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    except Exception:
        pass

    # DB sizes
    codegraph_dir = Path(root) / ".codegraph"
    db_sizes = {}
    total_size = 0
    for f in sorted(codegraph_dir.glob("*")) if codegraph_dir.exists() else []:
        if f.is_file() and not f.name.endswith(("-shm", "-wal")):
            size = f.stat().st_size
            total_size += size
            db_sizes[f.name] = size

    if args.json:
        print(
            json.dumps(
                {
                    "graph": {"nodes": graph, "edges": edges},
                    "fts": {"indexed_symbols": fts_count},
                    "calls": call_stats,
                    "storage": {k: v for k, v in db_sizes.items()},
                },
                indent=2,
            )
        )
        return

    console.print(LOGO)

    if graph_locked:
        console.print(
            Panel(
                "[yellow]Graph DB is locked (indexing in progress?). Showing FTS + call log stats only.[/yellow]",
                border_style="yellow",
            )
        )
    else:
        # Nodes table
        node_table = Table(
            title="Graph Nodes",
            box=box.SIMPLE_HEAD,
            title_style="bold cyan",
            show_lines=False,
        )
        node_table.add_column("Type", style="bold")
        node_table.add_column("Count", justify="right")
        node_table.add_column("", width=20)

        total_nodes = sum(graph.values())
        colors = {
            "File": "white",
            "Function": "green",
            "Class": "yellow",
            "TFResource": "magenta",
            "TFVar": "magenta",
            "MdSection": "cyan",
        }

        for label, count in graph.items():
            if count == 0:
                continue
            color = colors.get(label, "white")
            pct = count / total_nodes * 100 if total_nodes > 0 else 0
            bar_len = int(pct / 5)
            bar = f"[{color}]{'#' * bar_len}[/{color}][dim]{'.' * (20 - bar_len)}[/dim]"
            node_table.add_row(label, f"[{color}]{count:,}[/{color}]", bar)

        node_table.add_section()
        node_table.add_row("[bold]Total[/bold]", f"[bold]{total_nodes:,}[/bold]", "")
        console.print(node_table)

        # Edges table
        if edges:
            edge_table = Table(
                title="Graph Edges",
                box=box.SIMPLE_HEAD,
                title_style="bold cyan",
                show_lines=False,
            )
            edge_table.add_column("Relationship", style="bold")
            edge_table.add_column("Count", justify="right")

            for edge, count in sorted(edges.items(), key=lambda x: -x[1]):
                edge_table.add_row(edge, f"{count:,}")
            edge_table.add_section()
            edge_table.add_row("[bold]Total[/bold]", f"[bold]{sum(edges.values()):,}[/bold]")
            console.print(edge_table)

    # FTS + storage row
    info_table = Table(box=box.SIMPLE_HEAD, title="Index Info", title_style="bold cyan")
    info_table.add_column("", style="bold")
    info_table.add_column("", justify="right")
    info_table.add_row("FTS symbols", f"{fts_count:,}")
    for name, size in db_sizes.items():
        if size > 1024 * 1024:
            info_table.add_row(name, f"{size / 1024 / 1024:.1f} MB")
        else:
            info_table.add_row(name, f"{size / 1024:.0f} KB")
    if total_size > 0:
        info_table.add_section()
        if total_size > 1024 * 1024:
            info_table.add_row("[bold]Total storage[/bold]", f"[bold]{total_size / 1024 / 1024:.1f} MB[/bold]")
        else:
            info_table.add_row("[bold]Total storage[/bold]", f"[bold]{total_size / 1024:.0f} KB[/bold]")
    console.print(info_table)

    # Call stats
    if call_stats["total_calls"] > 0:
        call_table = Table(
            title="MCP Tool Calls",
            box=box.SIMPLE_HEAD,
            title_style="bold cyan",
        )
        call_table.add_column("Tool", style="bold")
        call_table.add_column("Calls", justify="right")
        call_table.add_column("Avg ms", justify="right")
        call_table.add_column("Max ms", justify="right")
        call_table.add_column("Errors", justify="right")

        for tool, ts in sorted(
            call_stats.get("tools", {}).items(),
            key=lambda x: -x[1]["calls"],
        ):
            err_str = f"[red]{ts['errors']}[/red]" if ts["errors"] > 0 else "[dim]0[/dim]"
            call_table.add_row(
                tool,
                str(ts["calls"]),
                f"{ts['avg_latency_ms']:.1f}",
                f"{ts['max_latency_ms']:.1f}",
                err_str,
            )

        call_table.add_section()
        call_table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{call_stats['total_calls']}[/bold]",
            "",
            "",
            f"[bold]{call_stats.get('error_count', 0)}[/bold] ({call_stats.get('error_rate', '0%')})",
        )
        if call_stats.get("period"):
            call_table.add_row(
                "[dim]Period[/dim]",
                f"[dim]{call_stats['period']['first_call']}[/dim]",
                "[dim]to[/dim]",
                f"[dim]{call_stats['period']['last_call']}[/dim]",
                "",
            )
        console.print(call_table)
    else:
        console.print("[dim]MCP tool calls: 0 (no calls logged yet)[/dim]")

    console.print()


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------


def cmd_logs(args) -> None:
    from codegraph.call_log import clear_logs, get_logs

    root = os.path.abspath(args.root)

    if args.clear:
        count = clear_logs(root)
        console.print(f"[yellow]Cleared {count} log entries.[/yellow]")
        return

    logs = get_logs(
        repo_root=root,
        tool=args.tool,
        limit=args.limit,
        errors_only=args.errors,
    )

    if not logs:
        console.print("[dim]No call logs found.[/dim]")
        return

    if args.json:
        print(json.dumps(logs, indent=2))
        return

    table = Table(
        title=f"Call Logs (last {len(logs)})",
        box=box.SIMPLE_HEAD,
        title_style="bold",
    )
    table.add_column("Time", style="dim", width=19)
    table.add_column("", width=3)
    table.add_column("Tool", style="bold")
    table.add_column("Latency", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Args", max_width=40, overflow="ellipsis")

    for entry in logs:
        status = Text("OK", style="green") if entry["success"] else Text("ERR", style="bold red")
        try:
            parsed = json.loads(entry["args"])
            args_str = " ".join(f"{k}={v}" for k, v in parsed.items())[:40]
        except (json.JSONDecodeError, TypeError):
            args_str = entry["args"][:40]

        latency_style = "red" if entry["latency_ms"] > 100 else "yellow" if entry["latency_ms"] > 20 else "green"

        table.add_row(
            entry["timestamp"],
            status,
            entry["tool"],
            f"[{latency_style}]{entry['latency_ms']:.1f}ms[/{latency_style}]",
            f"{entry['result_size']:,}B",
            f"[dim]{args_str}[/dim]",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# cmd_history
# ---------------------------------------------------------------------------


def cmd_history(args) -> None:
    """Show recent indexing activity grouped by day."""
    from datetime import datetime, timedelta

    root = os.path.abspath(args.root)
    days = args.days
    codegraph_dir = Path(root) / ".codegraph"
    log_path = codegraph_dir / "call_log.db"

    console.print(LOGO)

    if not log_path.exists():
        console.print("[dim]No call log found. MCP tools have not been called yet.[/dim]")
        return

    try:
        conn = sqlite3.connect(str(log_path))
    except Exception as exc:
        console.print(f"[red]Cannot open call_log.db: {exc}[/red]")
        return

    cutoff = (datetime.now() - timedelta(days=days)).timestamp()

    # Per-day stats
    rows = conn.execute(
        "SELECT date(timestamp, 'unixepoch', 'localtime') AS day, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors "
        "FROM call_log WHERE timestamp >= ? "
        "GROUP BY day ORDER BY day DESC",
        (cutoff,),
    ).fetchall()

    if not rows:
        console.print(f"[dim]No activity in the last {days} day(s).[/dim]")
        conn.close()
        return

    # Top tools per day
    day_tools = {}
    tool_rows = conn.execute(
        "SELECT date(timestamp, 'unixepoch', 'localtime') AS day, "
        "tool, COUNT(*) AS cnt "
        "FROM call_log WHERE timestamp >= ? "
        "GROUP BY day, tool ORDER BY day DESC, cnt DESC",
        (cutoff,),
    ).fetchall()

    for day, tool, cnt in tool_rows:
        day_tools.setdefault(day, []).append((tool, cnt))

    conn.close()

    table = Table(
        title=f"Activity — Last {days} Day(s)",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    table.add_column("Date", style="bold")
    table.add_column("Calls", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Top Tools")

    for day, total, errors in rows:
        err_str = f"[red]{errors}[/red]" if errors > 0 else "[dim]0[/dim]"
        top = day_tools.get(day, [])[:3]
        top_str = ", ".join(f"[cyan]{t}[/cyan]({c})" for t, c in top)
        table.add_row(day, str(total), err_str, top_str)

    console.print(table)

    # Grand total
    grand_total = sum(r[1] for r in rows)
    grand_errors = sum(r[2] for r in rows)
    console.print(f"\n[dim]Total: {grand_total} calls, {grand_errors} errors across {len(rows)} day(s)[/dim]")


# ---------------------------------------------------------------------------
# cmd_diff
# ---------------------------------------------------------------------------


def cmd_diff(args) -> None:
    """Show files changed since last index."""
    import subprocess

    root = os.path.abspath(args.root)
    since = args.since

    console.print(LOGO)
    console.print(f"  [dim]Repository:[/dim] [bold]{root}[/bold]\n")

    # Get parseable extensions
    from codegraph.parsers import get_supported_extensions

    supported = set(get_supported_extensions())

    # Changed files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since],
            capture_output=True,
            text=True,
            cwd=root,
        )
        changed_files = [f for f in result.stdout.strip().splitlines() if f]
    except FileNotFoundError:
        console.print("[red]git not found in PATH.[/red]")
        return

    # Untracked files
    try:
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        untracked_files = [f for f in result_untracked.stdout.strip().splitlines() if f]
    except FileNotFoundError:
        untracked_files = []

    # Categorize changed files
    parseable_changed = []
    other_changed = []
    for f in changed_files:
        suffix = Path(f).suffix.lower()
        if suffix in supported:
            parseable_changed.append(f)
        else:
            other_changed.append(f)

    # Categorize untracked files
    parseable_untracked = [f for f in untracked_files if Path(f).suffix.lower() in supported]

    if not changed_files and not parseable_untracked:
        console.print(f"[dim]No changes since {since}.[/dim]")
        return

    # Show parseable changed files
    if parseable_changed:
        table = Table(
            title=f"Changed Files (parseable) since {since}",
            box=box.SIMPLE_HEAD,
            title_style="bold cyan",
        )
        table.add_column("File", style="bold")
        table.add_column("Language")

        for f in sorted(parseable_changed):
            suffix = Path(f).suffix.lower()
            color = _lang_color(suffix)
            table.add_row(f"[{color}]{f}[/{color}]", f"[{color}]{suffix}[/{color}]")
        console.print(table)

    # Show non-parseable changed files
    if other_changed:
        console.print(f"\n[dim]  + {len(other_changed)} non-parseable changed file(s)[/dim]")

    # Show untracked parseable
    if parseable_untracked:
        console.print()
        table = Table(
            title="Unindexed New Files (parseable)",
            box=box.SIMPLE_HEAD,
            title_style="bold yellow",
        )
        table.add_column("File", style="bold")
        table.add_column("Language")

        for f in sorted(parseable_untracked):
            suffix = Path(f).suffix.lower()
            color = _lang_color(suffix)
            table.add_row(f"[{color}]{f}[/{color}]", f"[{color}]{suffix}[/{color}]")
        console.print(table)

    # Summary
    console.print()
    console.print(
        Panel(
            f"[bold]{len(parseable_changed)}[/bold] parseable changed  |  "
            f"[bold]{len(parseable_untracked)}[/bold] new unindexed  |  "
            f"[bold]{len(other_changed)}[/bold] other",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# cmd_doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args) -> None:
    """Health check — verify all codegraph components are working."""
    import shutil

    root = Path(os.path.abspath(args.root))
    codegraph_dir = root / ".codegraph"

    console.print(LOGO)
    console.print(f"  [dim]Project:[/dim] [bold]{root}[/bold]\n")

    checks: list[tuple[str, bool, str]] = []

    # 1. .codegraph/ exists
    cg_exists = codegraph_dir.exists() and codegraph_dir.is_dir()
    checks.append((".codegraph/ directory", cg_exists, "initialized" if cg_exists else "run 'cgh init' first"))

    # 2. graph.db accessible
    graph_ok = False
    graph_msg = "not found"
    graph_path = codegraph_dir / "graph.db"
    if graph_path.exists():
        try:
            from codegraph.db import get_readonly_connection

            conn = get_readonly_connection(root)
            if conn is not None:
                graph_ok = True
                graph_msg = "accessible"
            else:
                graph_msg = "locked by another process"
        except Exception as exc:
            graph_msg = f"error: {exc}"
    checks.append(("graph.db", graph_ok, graph_msg))

    # 3. fts.db accessible
    fts_ok = False
    fts_msg = "not found"
    fts_path = codegraph_dir / "fts.db"
    if fts_path.exists():
        try:
            from codegraph.fts import get_fts_conn

            fts_conn = get_fts_conn(root)
            fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
            fts_ok = True
            fts_msg = "accessible"
        except Exception as exc:
            fts_msg = f"error: {exc}"
    else:
        fts_msg = "not created yet (run 'cgh index')"
    checks.append(("fts.db", fts_ok, fts_msg))

    # 4. call_log.db accessible
    call_ok = False
    call_msg = "not found"
    call_path = codegraph_dir / "call_log.db"
    if call_path.exists():
        try:
            from codegraph.call_log import get_stats as _cl_stats

            _cl_stats(root)
            call_ok = True
            call_msg = "accessible"
        except Exception as exc:
            call_msg = f"error: {exc}"
    else:
        call_msg = "not created yet (created on first MCP call)"
    checks.append(("call_log.db", call_ok, call_msg))

    # 5. config.toml exists and valid
    config_ok = False
    config_msg = "not found"
    config_path = codegraph_dir / "config.toml"
    if config_path.exists():
        try:
            from codegraph.config import load_config

            load_config(root)
            config_ok = True
            config_msg = "valid"
        except Exception as exc:
            config_msg = f"parse error: {exc}"
    checks.append(("config.toml", config_ok, config_msg))

    # 6. Parsers can load
    parsers_ok = False
    parsers_msg = "import failed"
    try:
        from codegraph.parsers import get_parser_info

        info = get_parser_info()
        parsers_ok = True
        parsers_msg = f"{len(info)} parser(s) loaded"
    except Exception as exc:
        parsers_msg = f"error: {exc}"
    checks.append(("parsers", parsers_ok, parsers_msg))

    # 7. git available
    git_ok = shutil.which("git") is not None
    checks.append(("git", git_ok, "found" if git_ok else "not in PATH"))

    # 8. .cghignore exists
    cghignore_path = root / ".cghignore"
    cghignore_ok = cghignore_path.exists()
    checks.append((".cghignore", cghignore_ok, "found" if cghignore_ok else "not found (optional)"))

    # 9. MCP server (fastmcp import)
    mcp_ok = False
    mcp_msg = "fastmcp not installed"
    try:
        import fastmcp  # noqa: F401

        mcp_ok = True
        mcp_msg = "ready"
    except ImportError:
        mcp_msg = "fastmcp not installed"
    checks.append(("MCP server", mcp_ok, mcp_msg))

    # Display results
    table = Table(
        title="Health Check",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    table.add_column("Component", style="bold")
    table.add_column("", width=3)
    table.add_column("Status")

    pass_count = 0
    for name, ok, msg in checks:
        icon = "[green]OK[/green]" if ok else "[red]!![/red]"
        msg_style = "green" if ok else "yellow"
        table.add_row(name, icon, f"[{msg_style}]{msg}[/{msg_style}]")
        if ok:
            pass_count += 1

    console.print(table)

    # Overall
    total = len(checks)
    if pass_count == total:
        console.print(
            Panel(
                f"[bold green]All {total} checks passed.[/bold green]",
                border_style="green",
            )
        )
    else:
        failed = total - pass_count
        console.print(
            Panel(
                f"[bold yellow]{pass_count}/{total} checks passed, {failed} issue(s).[/bold yellow]",
                border_style="yellow",
            )
        )


# ---------------------------------------------------------------------------
# cmd_compact
# ---------------------------------------------------------------------------


def cmd_compact(args) -> None:
    """Vacuum SQLite DBs and show before/after sizes."""
    root = os.path.abspath(args.root)
    codegraph_dir = Path(root) / ".codegraph"

    console.print(LOGO)

    if not codegraph_dir.exists():
        console.print("[yellow].codegraph/ not found. Run 'cgh init' first.[/yellow]")
        return

    def _fmt_size(size_bytes: int) -> str:
        if size_bytes > 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.0f} KB"

    # DBs to vacuum (SQLite only — graph.db is Kuzu, not vacuumable here)
    sqlite_dbs = ["fts.db", "call_log.db"]
    results: list[tuple[str, int, int]] = []

    for db_name in sqlite_dbs:
        db_path = codegraph_dir / db_name
        if not db_path.exists():
            continue

        before = db_path.stat().st_size
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.close()
            after = db_path.stat().st_size
            results.append((db_name, before, after))
        except Exception as exc:
            console.print(f"  [red]x[/red] {db_name}: {exc}")

    # Graph.db size (read-only info)
    graph_path = codegraph_dir / "graph.db"
    graph_size = 0
    if graph_path.exists():
        if graph_path.is_dir():
            for f in graph_path.rglob("*"):
                if f.is_file():
                    graph_size += f.stat().st_size
        else:
            graph_size = graph_path.stat().st_size

    # Display
    table = Table(
        title="Compact Results",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    table.add_column("Database", style="bold")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Saved", justify="right")

    total_saved = 0
    for db_name, before, after in results:
        saved = before - after
        total_saved += saved
        saved_str = f"[green]-{_fmt_size(saved)}[/green]" if saved > 0 else "[dim]0[/dim]"
        table.add_row(db_name, _fmt_size(before), _fmt_size(after), saved_str)

    if graph_size > 0:
        table.add_row(
            "[dim]graph.db (Kuzu)[/dim]",
            _fmt_size(graph_size),
            "[dim]---[/dim]",
            "[dim]N/A[/dim]",
        )

    console.print(table)

    console.print(
        Panel(
            f"[bold]Reclaimed: {_fmt_size(total_saved)}[/bold]"
            if total_saved > 0
            else "[bold]Already compact — no space reclaimed.[/bold]",
            border_style="green" if total_saved > 0 else "dim",
        )
    )
