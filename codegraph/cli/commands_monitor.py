# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands: stats, logs, history, diff, doctor, compact.

from __future__ import annotations

from codegraph.core.utils import quiet_subprocess_kwargs

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codegraph.cli import LOGO, VERSION, _get_conn, _lang_color, console

# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------


def _build_stats_group(root: str) -> Group:
    """Build the stats display as a Rich Group, reusable for --live mode."""
    # Reset cached readonly connection so we see fresh counts every tick
    from codegraph.core.db import reset_connection as _rst

    _rst()
    return _stats_content(root)


def cmd_stats(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)

    if getattr(args, "json", False):
        print(_stats_json(root))
        return

    if getattr(args, "live", False):
        console.print(LOGO)
        console.print("[dim]Live stats (Ctrl-C to stop)[/dim]\n")
        try:
            with Live(
                _build_stats_group(root),
                console=console,
                refresh_per_second=2,
                screen=False,
            ) as live:
                import time as _t

                while True:
                    _t.sleep(0.5)
                    live.update(_build_stats_group(root))
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
        return

    console.print(LOGO)
    console.print(_stats_content(root))


def _stats_content(root: str) -> Group:
    """Produce the stats renderables for a repo as a Rich Group."""
    conn = _get_conn(root, readonly=True)

    graph: dict = {}
    edges: dict = {}
    graph_locked = conn is None

    if conn is not None:
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            try:
                graph[label] = conn.count_nodes(label)
            except Exception:
                pass

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
                c = conn.count_edges(edge_type)
                if c > 0:
                    edges[edge_type] = c
            except Exception:
                pass

    from codegraph.state.call_log import get_stats

    call_stats = get_stats(root)

    fts_count = 0
    try:
        from codegraph.core.fts import get_fts_conn

        fts_conn = get_fts_conn(root)
        fts_count = fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    except Exception:
        pass

    codegraph_dir = Path(root) / ".codegraph"
    db_sizes: dict = {}
    total_size = 0
    for f in sorted(codegraph_dir.glob("*")) if codegraph_dir.exists() else []:
        if f.is_file() and not f.name.endswith(("-shm", "-wal")):
            size = f.stat().st_size
            total_size += size
            db_sizes[f.name] = size

    renderables: list = []

    # Scan freshness banner
    try:
        from codegraph.state.scan_meta import scan_status as _scan_status

        ss = _scan_status(root)
        if ss.get("indexed_sha"):
            sha_short = (ss["indexed_sha"] or "")[:7]
            branch = ss.get("indexed_branch") or "?"
            dirty = ss.get("dirty")
            if ss.get("fresh"):
                msg = f"[green]fresh[/green], indexed at [bold]{sha_short}[/bold] on [bold]{branch}[/bold]"
            else:
                behind = ss.get("behind_by")
                curr = (ss.get("current_sha") or "")[:7]
                curr_branch = ss.get("current_branch") or "?"
                drift_bits = []
                if behind:
                    drift_bits.append(
                        f"{behind} commit{'s' if behind != 1 else ''} behind"
                    )
                if dirty:
                    drift_bits.append("working tree dirty")
                if branch != curr_branch:
                    drift_bits.append(f"branch {branch} → {curr_branch}")
                drift = ", ".join(drift_bits) or "drifted"
                msg = (
                    f"[yellow]stale[/yellow], indexed at [bold]{sha_short}[/bold] on [bold]{branch}[/bold]  "
                    f"[dim]→ HEAD {curr} ({drift})[/dim]  "
                    f"[dim]run scan_repo to refresh[/dim]"
                )
            renderables.append(Text.from_markup(msg))
    except Exception:
        pass

    if graph_locked:
        renderables.append(
            Panel(
                "[yellow]Graph DB is locked (indexing in progress?). Showing FTS + call log stats only.[/yellow]",
                border_style="yellow",
            )
        )
    else:
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
        renderables.append(node_table)

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
            edge_table.add_row(
                "[bold]Total[/bold]", f"[bold]{sum(edges.values()):,}[/bold]"
            )
            renderables.append(edge_table)

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
            info_table.add_row(
                "[bold]Total storage[/bold]",
                f"[bold]{total_size / 1024 / 1024:.1f} MB[/bold]",
            )
        else:
            info_table.add_row(
                "[bold]Total storage[/bold]", f"[bold]{total_size / 1024:.0f} KB[/bold]"
            )
    renderables.append(info_table)

    if call_stats["total_calls"] > 0:
        call_table = Table(
            title="MCP Tool Calls", box=box.SIMPLE_HEAD, title_style="bold cyan"
        )
        call_table.add_column("Tool", style="bold")
        call_table.add_column("Calls", justify="right")
        call_table.add_column("Avg ms", justify="right")
        call_table.add_column("Max ms", justify="right")
        call_table.add_column("Errors", justify="right")
        for tool, ts in sorted(
            call_stats.get("tools", {}).items(), key=lambda x: -x[1]["calls"]
        ):
            err_str = (
                f"[red]{ts['errors']}[/red]" if ts["errors"] > 0 else "[dim]0[/dim]"
            )
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
        renderables.append(call_table)
    else:
        renderables.append(
            Text.from_markup("[dim]MCP tool calls: 0 (no calls logged yet)[/dim]")
        )

    return Group(*renderables)


def _stats_json(root: str) -> str:
    """Produce the stats as a JSON string (for --json mode)."""
    conn = _get_conn(root, readonly=True)
    graph: dict = {}
    edges: dict = {}
    if conn is not None:
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            try:
                graph[label] = conn.count_nodes(label)
            except Exception:
                pass
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
                c = conn.count_edges(edge_type)
                if c > 0:
                    edges[edge_type] = c
            except Exception:
                pass

    from codegraph.state.call_log import get_stats

    call_stats = get_stats(root)

    fts_count = 0
    try:
        from codegraph.core.fts import get_fts_conn

        fts_conn = get_fts_conn(root)
        fts_count = fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    except Exception:
        pass

    codegraph_dir = Path(root) / ".codegraph"
    db_sizes: dict = {}
    for f in sorted(codegraph_dir.glob("*")) if codegraph_dir.exists() else []:
        if f.is_file() and not f.name.endswith(("-shm", "-wal")):
            db_sizes[f.name] = f.stat().st_size

    return json.dumps(
        {
            "graph": {"nodes": graph, "edges": edges},
            "fts": {"indexed_symbols": fts_count},
            "calls": call_stats,
            "storage": db_sizes,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def _empty_status_source() -> dict:
    """The default 'nothing resolved' status-source dict.

    counts_source == "none" is the sentinel cmd_status checks to decide
    whether to fall through to the next tier.
    """
    return {
        "file_count": 0,
        "endpoint_count": 0,
        "counts_source": "none",
        "fts_symbols": None,
    }


def _status_via_owner(root: str, owner_alive: bool, owner_port: int | None) -> dict:
    """Tier 1: ask a live owner for counts via the live_graph_stats MCP tool.

    Only attempted when the owner is alive and its port is known. Returns a
    status-source dict, counts_source == "owner" on success, else "none".
    The owner's live stats do not include an endpoint count, so it stays 0.
    """
    src = _empty_status_source()
    if not (owner_alive and owner_port):
        return src
    try:
        stats = _ask_owner_live_stats(root, owner_port)
        if stats is not None:
            nodes = stats.get("nodes") or {}
            src["file_count"] = int(nodes.get("File", 0))
            # endpoint count not in live_graph_stats, derive separately if 0
            src["fts_symbols"] = int(stats.get("fts_symbols", 0))
            src["counts_source"] = "owner"
    except Exception:
        pass
    return src


def _status_via_ro_open(root: str) -> dict:
    """Tier 2: open the graph DB read-only and count File / Endpoint nodes.

    Works only when no owner holds the write lock. Returns a status-source
    dict, counts_source == "ro" on success, else "none".
    """
    src = _empty_status_source()
    try:
        from codegraph.core.db import get_readonly_connection

        conn = get_readonly_connection(root)
        if conn is not None:
            src["file_count"] = conn.count_nodes("File")
            src["endpoint_count"] = conn.count_nodes("Endpoint")
            src["counts_source"] = "ro"
    except Exception:
        pass
    return src


def _status_via_fts(root: str) -> dict:
    """Tier 3: count symbols straight from the FTS sqlite (always RO-safe).

    Final fallback when both the owner and a RO graph open are unavailable.
    Returns a status-source dict, counts_source == "fts_only" on success,
    else "none".
    """
    src = _empty_status_source()
    try:
        import sqlite3 as _sql

        fts_path = Path(root) / ".codegraph" / "fts.db"
        if fts_path.exists():
            from codegraph.core.utils import ro_sqlite_uri

            c = _sql.connect(ro_sqlite_uri(fts_path), uri=True)
            src["fts_symbols"] = c.execute("SELECT count(*) FROM symbols").fetchone()[0]
            c.close()
            src["counts_source"] = "fts_only"
    except Exception:
        pass
    return src


def cmd_status(args: argparse.Namespace) -> None:
    """Quick one-screen health check: owner, freshness, counts, extra_dirs."""
    import json as _json

    from codegraph.state.ipc import (
        is_owner_alive,
        live_workers,
        read_owner_port,
    )
    from codegraph.state.scan_meta import scan_status as _scan_status

    root = os.path.abspath(args.root)

    # Owner
    owner_pid = None
    owner_port = None
    if (Path(root) / ".codegraph").exists():
        try:
            owner_pid = int(
                (Path(root) / ".codegraph" / "owner.pid")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, ValueError):
            pass
        owner_port = read_owner_port(root)
    owner_alive = is_owner_alive(root)
    workers = live_workers(root)

    # --refresh: ask the owner to run incremental_reindex BEFORE we read
    # scan_meta. The watcher keeps individual files fresh on each save but
    # never advances scan_meta.git_head, running incremental_reindex does,
    # by checking every file's blob against HEAD and recording the new SHA
    # when they all match.
    if getattr(args, "refresh", False):
        if not owner_alive or not owner_port:
            console.print("[yellow]Cannot refresh: owner is not running.[/yellow]")
            console.print(
                "[dim]Start it with:[/dim] [cyan]cgh serve --background --watch[/cyan] [dim]then re-run.[/dim]\n"
            )
        else:
            console.print(
                "[dim]Calling incremental_reindex via owner, verifying every file's blob against HEAD…[/dim]"
            )
            stats = _ask_owner_incremental_reindex(root, owner_port)
            if stats is None:
                console.print(
                    "[yellow]Refresh call failed (timeout or error).[/yellow]\n"
                )
            else:
                rx = stats.get("reindexed_count", stats.get("indexed", 0))
                un = stats.get("unchanged_count", stats.get("skipped", 0))
                de = stats.get("deleted_count", 0)
                el = stats.get("elapsed_s", "?")
                console.print(
                    f"[green]✓[/green] reindexed={rx}, unchanged={un}, deleted={de}, elapsed={el}s\n"
                )

    # Scan freshness (re-read after possible refresh)
    ss = _scan_status(root)

    # Counts. Kuzu holds an exclusive lock for the lifetime of an open
    # write Database, so when our owner is alive, even a read-only open
    # from this CLI process is blocked. Order of attempts:
    #   1. If the owner is alive AND we know its port, ask it via MCP
    #      (live_graph_stats), authoritative + cheap.
    #   2. Else try a local RO open (works only when no owner is up).
    #   3. As a final fallback, read the FTS sqlite (always RO-safe).
    # Each tier is a helper returning a status-source dict, we take the
    # first one that resolved (counts_source != "none").
    src = _status_via_owner(root, owner_alive, owner_port)
    if src["counts_source"] == "none":
        src = _status_via_ro_open(root)
    if src["counts_source"] == "none":
        src = _status_via_fts(root)

    file_count = src["file_count"]
    endpoint_count = src["endpoint_count"]
    counts_source = src["counts_source"]
    fts_symbols = src["fts_symbols"]
    extra_dirs: list[str] = []

    try:
        import tomllib

        cfg = Path(root) / ".codegraph" / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                extra_dirs = tomllib.load(f).get("codegraph", {}).get("extra_dirs", [])
    except Exception:
        pass

    # Federated subrepos, surface them in status so users see at a glance
    # which children this owner fans queries out to and whether they're up.
    subrepos: list[dict] = []
    try:
        from codegraph.analysis.federation import (
            child_owner_status,
            resolve_children,
            verify_child,
        )

        for child in resolve_children(root):
            st = verify_child(child)
            owner = child_owner_status(child) if st.ok else None
            subrepos.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "ok": st.ok,
                    "status": (
                        "ok"
                        if st.ok
                        else "no graph.db"
                        if st.initialized and not st.has_kuzu
                        else "uninitialized"
                        if st.exists
                        else "missing"
                    ),
                    "owner_alive": bool(owner and owner.alive),
                    "owner_port": owner.port if owner and owner.alive else None,
                }
            )
    except Exception:
        pass

    payload = {
        "version": VERSION,
        "backend": _backend_info(root),
        "root": root,
        "owner": {
            "alive": owner_alive,
            "pid": owner_pid,
            "port": owner_port,
            "workers": len(workers),
        },
        "scan": {
            "fresh": ss.get("fresh"),
            "indexed_sha": (ss.get("indexed_sha") or "")[:8] or None,
            "indexed_branch": ss.get("indexed_branch"),
            "indexed_at": ss.get("indexed_at"),
            "current_sha": (ss.get("current_sha") or "")[:8] or None,
            "dirty": ss.get("dirty"),
            "behind_by": ss.get("behind_by"),
        },
        "graph": {
            "files": file_count,
            "endpoints": endpoint_count,
        },
        "extra_dirs": extra_dirs,
        "subrepos": subrepos,
    }

    if getattr(args, "json", False):
        print(_json.dumps(payload, indent=2))
        return

    console.print(LOGO)

    # Owner panel
    if owner_alive:
        owner_line = f"[green]running[/green]  pid={owner_pid} port={owner_port}  {len(workers)} worker(s)"
    elif owner_pid:
        owner_line = f"[yellow]stale pidfile[/yellow]  pid={owner_pid} (not alive)"
    else:
        owner_line = "[dim]not running[/dim]"

    # Freshness
    if ss.get("fresh"):
        scan_line = (
            f"[green]fresh[/green]  indexed [bold]{payload['scan']['indexed_sha']}[/bold] "
            f"on [bold]{ss.get('indexed_branch') or '?'}[/bold]"
        )
    elif payload["scan"]["indexed_sha"]:
        drift = []
        if ss.get("behind_by"):
            drift.append(
                f"{ss['behind_by']} commit{'s' if ss['behind_by'] != 1 else ''} behind"
            )
        if ss.get("dirty"):
            drift.append("working tree dirty")
        scan_line = (
            f"[yellow]stale[/yellow]  indexed [bold]{payload['scan']['indexed_sha']}[/bold] → "
            f"HEAD [bold]{payload['scan']['current_sha']}[/bold]"
            + (f"  ({', '.join(drift)})" if drift else "")
        )
    else:
        scan_line = "[dim]no scan recorded, run cgh index[/dim]"

    table = Table(
        box=box.SIMPLE_HEAD, title="codegraph status", title_style="bold cyan"
    )
    table.add_column("", style="bold")
    table.add_column("", overflow="fold")
    table.add_row("Version", f"[cyan]{VERSION}[/cyan]")
    table.add_row("Backend", _backend_status_line(root))
    table.add_row("Owner", owner_line)
    table.add_row("Scan", scan_line)
    fts_suffix = f"  [dim]· FTS {fts_symbols:,} symbols[/dim]" if fts_symbols else ""
    if counts_source == "owner":
        files_cell = f"{file_count:,}{fts_suffix}  [dim](via owner)[/dim]"
        endpoints_cell = "[dim]ask the MCP `endpoints` tool[/dim]"
    elif counts_source == "ro":
        files_cell = f"{file_count:,}{fts_suffix}"
        endpoints_cell = f"{endpoint_count:,}"
    elif counts_source == "fts_only":
        files_cell = f"[dim]graph locked[/dim]{fts_suffix}"
        endpoints_cell = "[dim]unknown (graph locked)[/dim]"
    else:
        files_cell = "[dim]unknown[/dim]"
        endpoints_cell = "[dim]unknown[/dim]"
    table.add_row("Files", files_cell)
    table.add_row("Endpoints", endpoints_cell)
    table.add_row(
        "Extra dirs", ", ".join(extra_dirs) if extra_dirs else "[dim]none[/dim]"
    )
    table.add_row("Subrepos", _format_subrepos_cell(subrepos))
    console.print(table)

    # --workers: detailed proxy list with tty + start time + cmdline
    if getattr(args, "workers", False):
        _print_workers_table(workers, owner_pid)


def _backend_info(root: str) -> dict:
    """Structured backend info for the --json payload.

    Always returns the same shape, caller can switch on ``on_disk`` to
    distinguish the empty case without checking for missing keys.
    """
    cg = Path(root) / ".codegraph"
    duck = cg / "graph.duckdb"
    kuzu_file = cg / "graph.db"

    on_disk: list[str] = []
    if duck.exists():
        on_disk.append("duckdb")
    if kuzu_file.exists():
        on_disk.append("kuzu")

    env_value = (os.environ.get("CGH_DB") or "").strip().lower()
    env_backend = "duckdb" if env_value == "duckdb" else "kuzu"

    if not on_disk:
        active = None
    elif len(on_disk) == 1:
        active = on_disk[0]
    else:
        # Tie-break matches _detect_backend_file in federation.py.
        active = "duckdb"

    return {
        "on_disk": on_disk,
        "active": active,
        "env_var": env_value or None,
        "env_implies": env_backend if env_value else None,
        "mismatch": bool(active and env_value and env_backend != active),
    }


def _backend_status_line(root: str) -> str:
    """One-line description of which graph backend is in use for ``root``.

    Reports:
      - the backend currently on disk (graph.duckdb / graph.db / none)
      - file size for the present DB
      - the CGH_DB env var if set, with a warning when it disagrees with
        what's on disk (the next `cgh index` would write a second file)
    """
    cg = Path(root) / ".codegraph"
    duck = cg / "graph.duckdb"
    kuzu_file = cg / "graph.db"

    on_disk: list[tuple[str, Path]] = []
    if duck.exists():
        on_disk.append(("duckdb", duck))
    if kuzu_file.exists():
        on_disk.append(("kuzu", kuzu_file))

    env_value = (os.environ.get("CGH_DB") or "").strip().lower()
    env_backend = "duckdb" if env_value == "duckdb" else "kuzu"
    env_was_set = bool(env_value)

    if not on_disk:
        if env_was_set:
            return (
                f"[dim]none on disk[/dim]  "
                f"[dim](CGH_DB={env_value!r}, next `cgh index` writes a {env_backend} DB)[/dim]"
            )
        return "[dim]none on disk[/dim]  [dim](would create graph.duckdb)[/dim]"

    def _size(p: Path) -> str:
        size = p.stat().st_size
        if size > 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{size / 1024:.0f} KB"

    if len(on_disk) == 1:
        kind, path = on_disk[0]
        colour = "green" if kind == "duckdb" else "magenta"
        label = f"[{colour}]{kind}[/{colour}] ([dim]{path.name}, {_size(path)}[/dim])"
        if env_was_set and env_backend != kind:
            return (
                label
                + f"  [yellow]CGH_DB={env_value!r} mismatch, next index would create a {env_backend} DB[/yellow]"
            )
        if kind == "kuzu":
            # Gentle nudge toward DuckDB, about 18x faster + 5x smaller
            # on the wb-backend stress test. Opt-in, not automatic.
            label += "  [dim]· run [cyan]cgh migrate-to-duckdb[/cyan] for faster + smaller DB[/dim]"
        return label

    # Both present (rare, half-migrated repo).
    duck_p = on_disk[0][1]
    kuzu_p = on_disk[1][1]
    label = (
        f"[yellow]both[/yellow] ([dim]graph.duckdb {_size(duck_p)}, "
        f"graph.db {_size(kuzu_p)}[/dim])"
    )
    if env_was_set:
        label += f"  [dim]CGH_DB={env_value!r} -> {env_backend} wins[/dim]"
    else:
        label += "  [dim]duckdb wins by default detection[/dim]"
    return label


def _format_subrepos_cell(subrepos: list[dict]) -> str:
    """One-liner for the Subrepos row in `cgh status`.

    Compact: `2 federated · ondonne-frontend [up :54052], ondonne-infra [down]`.
    Empty: dim "none".
    """
    if not subrepos:
        return "[dim]none[/dim]"
    parts: list[str] = []
    for s in subrepos:
        name = s["name"]
        if not s["ok"]:
            badge = f"[yellow]{s['status']}[/yellow]"
        elif s["owner_alive"]:
            badge = f"[green]up :{s['owner_port']}[/green]"
        else:
            badge = "[dim]down[/dim]"
        parts.append(f"{name} {badge}")
    return f"{len(subrepos)} federated · " + ", ".join(parts)


def _ask_owner_incremental_reindex(root: str, port: int) -> dict | None:
    """
    HTTP-call the owner's `incremental_reindex` MCP tool. This compares every
    File node's stored git blob SHA to the current HEAD blob, re-indexes any
    that drifted, and writes scan_meta with HEAD's SHA on success, which is
    what `cgh status --refresh` needs to advance the recorded SHA.

    Generous timeout (5 min) since on a large repo with many drifted files
    this can take a while. Returns the parsed stats dict or None on failure.
    """
    return _call_owner_tool(root, port, "incremental_reindex", timeout=300.0)


def _ask_owner_live_stats(root: str, port: int, timeout: float = 1.5) -> dict | None:
    return _call_owner_tool(root, port, "live_graph_stats", timeout=timeout)


def _call_owner_tool(root: str, port: int, tool: str, timeout: float) -> dict | None:
    """
    HTTP-call any MCP tool on the running owner with the given timeout.
    Returns the parsed JSON dict on success, None on any failure (timeout,
    auth, HTTP error, malformed body). Used by cgh status when the owner
    is alive and the local CLI can't open Kuzu read-only.
    """
    import http.client
    import json as _json

    from codegraph.state.auth import ensure_auth_key

    try:
        token = ensure_auth_key(root)
    except Exception:
        return None
    body = _json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {}},
        }
    )
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
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
        if resp.status != 200:
            return None
        raw = resp.read().decode("utf-8", errors="replace")
        c.close()
    except Exception:
        return None

    # MCP returns either a JSON object or an SSE stream depending on
    # the Accept header negotiation. Find the JSON-RPC envelope either way.
    payload = None
    if raw.startswith("{"):
        try:
            payload = _json.loads(raw)
        except Exception:
            return None
    else:
        # SSE: lines start with `data: `, last `data:` line carries the result
        for line in raw.splitlines():
            if line.startswith("data: "):
                try:
                    payload = _json.loads(line[6:])
                except Exception:
                    pass
    if not payload:
        return None
    content = (payload.get("result") or {}).get("content") or []
    text = next((c["text"] for c in content if c.get("type") == "text"), None)
    if not text:
        return None
    try:
        return _json.loads(text)
    except Exception:
        return None


def _print_workers_table(worker_pids: list[int], owner_pid: int | None) -> None:
    """Render a table of every proxy worker with ps metadata."""
    import subprocess

    pids = list(worker_pids)
    if owner_pid and owner_pid not in pids:
        pids.append(owner_pid)

    if not pids:
        console.print("\n[dim]No workers registered.[/dim]")
        return

    # One ps call, many PIDs → fewer subprocess invocations
    try:
        r = subprocess.run(
            [
                "ps",
                "-o",
                "pid=,tty=,lstart=,command=",
                "-p",
                ",".join(str(p) for p in pids),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            **quiet_subprocess_kwargs(),
        )
        lines = [ln.rstrip() for ln in r.stdout.splitlines() if ln.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        lines = []

    info: dict[int, tuple[str, str, str]] = {}
    for line in lines:
        # pid tty lstart(5 tokens) command(rest)
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        tty = parts[1]
        lstart = " ".join(parts[2:6])
        cmd = parts[6]
        info[pid] = (tty, lstart, cmd)

    tbl = Table(
        box=box.SIMPLE_HEAD,
        title="workers + owner",
        title_style="bold cyan",
    )
    tbl.add_column("role", style="bold", width=8)
    tbl.add_column("pid", justify="right", width=7)
    tbl.add_column("tty", width=10)
    tbl.add_column("started", style="dim", width=20)
    tbl.add_column("command", overflow="fold", style="dim")

    if owner_pid:
        tty, lstart, cmd = info.get(owner_pid, ("?", "?", "?"))
        tbl.add_row("[green]owner[/green]", str(owner_pid), tty, lstart, cmd)

    for pid in worker_pids:
        tty, lstart, cmd = info.get(pid, ("?", "?", "?"))
        tbl.add_row("proxy", str(pid), tty, lstart, cmd)

    console.print()
    console.print(tbl)


# ---------------------------------------------------------------------------
# cmd_reset
# ---------------------------------------------------------------------------


def cmd_reset(args: argparse.Namespace) -> None:
    """
    Nuke the graph + FTS DBs, kill the owner, then optionally re-index
    and re-publish. Use after a schema migration or when the graph gets
    into a weird state.
    """
    import shutil
    import subprocess

    from codegraph.state.ipc import owner_pidfile

    root = Path(os.path.abspath(args.root))
    cg_dir = root / ".codegraph"
    if not cg_dir.exists():
        console.print("[yellow]Not initialized.[/yellow]")
        return

    # 1. Kill the owner if it's running
    owner_pid_path = owner_pidfile(root)
    killed = False
    if owner_pid_path.exists():
        try:
            pid = int(owner_pid_path.read_text(encoding="utf-8").strip())
            # Cross-platform: graceful on POSIX, TerminateProcess on Windows.
            from codegraph.state.pidfile import terminate

            terminate(pid, graceful_timeout=3.0)
            killed = True
        except (ValueError, ProcessLookupError, OSError):
            pass

    # Defensive: kill any stray cgh serve / owner. pkill is POSIX-only, so it
    # is missing on native Windows and in Git Bash (raising FileNotFoundError);
    # the pidfile-based stop above already handled the known owner, so just
    # skip the name-based sweep where pkill is unavailable.
    try:
        subprocess.run(
            ["pkill", "-9", "-f", "codegraph _serve_owner"],
            capture_output=True,
            timeout=3,
            **quiet_subprocess_kwargs(),
        )
    except (FileNotFoundError, OSError):
        pass

    # 2. Confirm destructive deletion
    targets = []
    for name in ("graph.db", "fts.db", "scan_meta.json", "activity.log"):
        p = cg_dir / name
        if p.exists():
            targets.append(p)
    # Kuzu also writes .wal / .tmp / shm files
    for p in cg_dir.iterdir():
        if p.is_file() and (
            p.name.startswith("graph.db") or p.name.startswith("fts.db")
        ):
            if p not in targets:
                targets.append(p)
    # Workers dir + port + pid files (leftovers)
    for name in ("server.pid", "server.port", "owner.pid", "workers"):
        p = cg_dir / name
        if p.exists():
            targets.append(p)

    if not targets:
        console.print("[dim]Nothing to delete.[/dim]")
    else:
        if not args.yes:
            console.print("[yellow]Will delete:[/yellow]")
            for t in targets:
                console.print(f"  - {t.relative_to(root)}")
            if killed:
                console.print(f"[dim]Already stopped owner (pid {pid}).[/dim]")
            resp = input("Proceed? [y/N] ").strip().lower()
            if resp not in ("y", "yes"):
                console.print("[dim]Aborted.[/dim]")
                return

        for t in targets:
            try:
                if t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
                else:
                    t.unlink(missing_ok=True)
            except OSError:
                pass
        console.print(f"[green]Cleaned {len(targets)} items.[/green]")

    # 3. Optionally drop extra_dirs from config.toml
    if args.drop_extra_dirs:
        config_path = cg_dir / "config.toml"
        if config_path.exists():
            content = config_path.read_text(encoding="utf-8")
            new_content = re.sub(
                r"^\s*extra_dirs\s*=\s*\[.*?\]\s*\n",
                "",
                content,
                flags=re.MULTILINE | re.DOTALL,
            )
            if new_content != content:
                config_path.write_text(new_content, encoding="utf-8")
                console.print("[green]Dropped extra_dirs from config.toml[/green]")

    # 4. Re-index (unless --no-reindex)
    if not args.no_reindex:
        from codegraph.cli.commands_index import cmd_index

        cmd_index(
            __import__("argparse").Namespace(
                root=str(root),
                verbose=False,
                method="auto",
            )
        )


def _pid_alive(pid: int) -> bool:
    # Cross-platform; os.kill(pid, 0) would terminate the process on Windows.
    from codegraph.state.pidfile import process_alive

    return process_alive(pid)


# ---------------------------------------------------------------------------
# cmd_tail
# ---------------------------------------------------------------------------


def cmd_tail(args: argparse.Namespace) -> None:
    """Live view of scan/watcher activity. Works while MCP server is running."""
    import datetime as _dt
    import time as _t

    from codegraph.state.activity import tail as _act_tail

    root = os.path.abspath(args.root)
    n = getattr(args, "limit", 30)

    _EVENT_STYLE = {
        "scan_start": "bold cyan",
        "scan_end": "bold green",
        "scan_progress": "cyan",
        "scan_error": "red",
        "reindex": "green",
        "error": "red",
    }

    def _build():
        entries = _act_tail(root, n=n)
        table = Table(
            title="codegraph activity",
            box=box.SIMPLE_HEAD,
            title_style="bold cyan",
        )
        table.add_column("When", style="dim", width=8)
        table.add_column("Event", width=14)
        table.add_column("Detail", overflow="fold")
        if not entries:
            table.add_row("--:--:--", "[dim]no activity yet[/dim]", "")
        now = _t.time()
        for ts, event, detail in entries:
            age = now - ts
            if age < 60:
                when = f"{int(age)}s ago"
            elif age < 3600:
                when = f"{int(age / 60)}m ago"
            else:
                when = _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            style = _EVENT_STYLE.get(event, "white")
            table.add_row(when, f"[{style}]{event}[/{style}]", detail)
        return table

    if not getattr(args, "follow", False):
        console.print(_build())
        return

    console.print("[dim]Tailing codegraph activity (Ctrl-C to stop)[/dim]\n")
    try:
        with Live(
            _build(), console=console, refresh_per_second=2, screen=False
        ) as live:
            while True:
                _t.sleep(0.5)
                live.update(_build())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------


def cmd_logs(args: argparse.Namespace) -> None:
    from codegraph.state.call_log import clear_logs, get_logs

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

    if args.json:
        print(json.dumps(logs, indent=2))
        return

    console.print(LOGO)

    if not logs:
        console.print("[dim]No call logs found.[/dim]")
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
        status = (
            Text("OK", style="green")
            if entry["success"]
            else Text("ERR", style="bold red")
        )
        try:
            parsed = json.loads(entry["args"])
            args_str = " ".join(f"{k}={v}" for k, v in parsed.items())[:40]
        except (json.JSONDecodeError, TypeError):
            args_str = entry["args"][:40]

        latency_style = (
            "red"
            if entry["latency_ms"] > 100
            else "yellow"
            if entry["latency_ms"] > 20
            else "green"
        )

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


def cmd_history(args: argparse.Namespace) -> None:
    """Show recent indexing activity grouped by day."""
    from datetime import datetime, timedelta

    root = os.path.abspath(args.root)
    days = args.days
    codegraph_dir = Path(root) / ".codegraph"
    log_path = codegraph_dir / "call_log.db"

    console.print(LOGO)

    if not log_path.exists():
        console.print(
            "[dim]No call log found. MCP tools have not been called yet.[/dim]"
        )
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
        title=f"Activity: Last {days} Day(s)",
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
    console.print(
        f"\n[dim]Total: {grand_total} calls, {grand_errors} errors across {len(rows)} day(s)[/dim]"
    )


# ---------------------------------------------------------------------------
# cmd_diff
# ---------------------------------------------------------------------------


def cmd_diff(args: argparse.Namespace) -> None:
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
            encoding="utf-8",
            errors="replace",
            cwd=root,
            **quiet_subprocess_kwargs(),
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
            encoding="utf-8",
            errors="replace",
            cwd=root,
            **quiet_subprocess_kwargs(),
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
    parseable_untracked = [
        f for f in untracked_files if Path(f).suffix.lower() in supported
    ]

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
        console.print(
            f"\n[dim]  + {len(other_changed)} non-parseable changed file(s)[/dim]"
        )

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


def cmd_doctor(args: argparse.Namespace) -> None:
    """Health check, verify all codegraph components are working."""
    import shutil

    root = Path(os.path.abspath(args.root))
    codegraph_dir = root / ".codegraph"

    console.print(LOGO)
    console.print(f"  [dim]Project:[/dim] [bold]{root}[/bold]\n")

    checks: list[tuple[str, bool, str]] = []

    # 1. .codegraph/ exists
    cg_exists = codegraph_dir.exists() and codegraph_dir.is_dir()
    checks.append(
        (
            ".codegraph/ directory",
            cg_exists,
            "initialized" if cg_exists else "run 'cgh init' first",
        )
    )

    # 2. graph.db accessible
    graph_ok = False
    graph_msg = "not found"
    graph_path = codegraph_dir / "graph.db"
    if graph_path.exists():
        try:
            from codegraph.core.db import get_readonly_connection

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
            from codegraph.core.fts import get_fts_conn

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
            from codegraph.state.call_log import get_stats as _cl_stats

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
            from codegraph.core.config import load_config

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
    checks.append(
        (
            ".cghignore",
            cghignore_ok,
            "found" if cghignore_ok else "not found (optional)",
        )
    )

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

    # Claude Code integration audit, separate section since it answers
    # a different question (is what's installed in .claude/ up-to-date with
    # this cgh version) and the fix command is also different (cgh setup
    # claude, not anything in this repo's .codegraph/).
    try:
        from codegraph.cli.commands_init import audit_claude_integration

        audit = audit_claude_integration(root)
    except Exception:
        audit = None

    if audit is not None:
        _print_claude_audit(audit)


def _print_claude_audit(audit: dict) -> None:
    """Render the Claude Code integration audit produced by
    audit_claude_integration() as a Rich table with a fix hint when stale."""
    icon = {
        "ok": "[green]OK[/green]",
        "missing": "[red]!![/red]",
        "partial": "[yellow]~~[/yellow]",
        "stale": "[yellow]~~[/yellow]",
        "misplaced": "[yellow]~~[/yellow]",
    }

    tbl = Table(
        title="Claude Code integration",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    tbl.add_column("Artifact", style="bold")
    tbl.add_column("", width=3)
    tbl.add_column("Detail")

    m = audit["mcp_json"]
    tbl.add_row(
        ".mcp.json (codegraph entry)",
        icon.get(m["status"], "[dim]?[/dim]"),
        "[green]present[/green]" if m["status"] == "ok" else "[red]missing[/red]",
    )

    h = audit["hooks"]
    if h["status"] == "ok":
        detail = f"[green]{h['installed']}/{h['expected']} hooks installed[/green]"
    elif h["status"] == "misplaced":
        misplaced = ", ".join(h.get("misplaced", []))
        detail = (
            f"[yellow]{h['installed']}/{h['expected']} installed[/yellow]  "
            f"[dim](in wrong settings file: {misplaced})[/dim]"
        )
    else:
        bits = []
        if h.get("missing"):
            bits.append(f"missing: {', '.join(h['missing'])}")
        if h.get("misplaced"):
            bits.append(f"wrong file: {', '.join(h['misplaced'])}")
        detail = f"[yellow]{h['installed']}/{h['expected']} installed[/yellow]  [dim]({'; '.join(bits)})[/dim]"
    tbl.add_row(
        ".claude hooks (settings + local)",
        icon.get(h["status"], "[dim]?[/dim]"),
        detail,
    )

    s = audit["skills"]
    bits = [f"{s['installed']}/{s['bundled']} installed"]
    if s["missing"]:
        bits.append(f"missing: {', '.join(s['missing'])}")
    if s["modified"]:
        bits.append(f"modified: {', '.join(s['modified'])}")
    detail = (
        ("[green]" if s["status"] == "ok" else "[yellow]")
        + ", ".join(bits)
        + ("[/green]" if s["status"] == "ok" else "[/yellow]")
    )
    tbl.add_row(".claude/skills/", icon.get(s["status"], "[dim]?[/dim]"), detail)

    u = audit["usage_block"]
    tbl.add_row(
        "CLAUDE.md usage block",
        icon.get(u["status"], "[dim]?[/dim]"),
        "[green]present[/green]" if u["status"] == "ok" else "[red]missing[/red]",
    )

    console.print()
    console.print(tbl)

    if audit["overall"] != "ok":
        console.print(
            Panel(
                "[yellow]Drift detected.[/yellow] Run [cyan]cgh setup claude[/cyan] "
                "to install / refresh the missing or stale artifacts.",
                border_style="yellow",
            )
        )


# ---------------------------------------------------------------------------
# cmd_compact
# ---------------------------------------------------------------------------


def cmd_compact(args: argparse.Namespace) -> None:
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

    # DBs to vacuum (SQLite only, graph.db is Kuzu, not vacuumable here)
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
        saved_str = (
            f"[green]-{_fmt_size(saved)}[/green]" if saved > 0 else "[dim]0[/dim]"
        )
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
            else "[bold]Already compact, no space reclaimed.[/bold]",
            border_style="green" if total_saved > 0 else "dim",
        )
    )
