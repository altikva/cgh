# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh migrate-to-duckdb` — re-index a repo currently on the
# Kuzu backend into DuckDB, verify counts match, optionally delete the
# old graph.db. Safe to run mid-flight: keeps the old DB around until
# the user confirms.

from __future__ import annotations

import os
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, console

_DB_DIR = ".codegraph"
_KUZU_FILE = "graph.db"
_DUCKDB_FILE = "graph.duckdb"


def _size_str(size_bytes: int) -> str:
    if size_bytes > 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def _stats_snapshot(repo_root: Path) -> dict:
    """Read current node + edge counts from whichever backend the repo
    is on. Uses auto-detect so it follows the CGH_DB env var or on-disk
    file presence."""
    from codegraph.core.db import get_readonly_connection, reset_connection

    reset_connection()
    conn = get_readonly_connection(repo_root)
    if conn is None:
        return {"nodes": {}, "edges": {}}
    nodes: dict[str, int] = {}
    edges: dict[str, int] = {}
    for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
        try:
            nodes[label] = conn.count_nodes(label)
        except Exception:
            nodes[label] = 0
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
            edges[edge_type] = conn.count_edges(edge_type)
        except Exception:
            edges[edge_type] = 0
    reset_connection()
    return {"nodes": nodes, "edges": edges}


def _diff_stats(kuzu: dict, duckdb: dict) -> list[tuple[str, int, int]]:
    """Return [(metric, kuzu_count, duckdb_count), ...] for any row that
    differs between the two snapshots."""
    diffs: list[tuple[str, int, int]] = []
    for kind in ("nodes", "edges"):
        all_keys = sorted(set(kuzu.get(kind, {}).keys()) | set(duckdb.get(kind, {}).keys()))
        for key in all_keys:
            k = kuzu.get(kind, {}).get(key, 0)
            d = duckdb.get(kind, {}).get(key, 0)
            if k != d:
                diffs.append((f"{kind}.{key}", k, d))
    return diffs


def cmd_migrate_to_duckdb(args) -> None:
    """Re-index a Kuzu-backed repo into DuckDB and verify the migration.

    Workflow:
      1. Check that ``.codegraph/graph.db`` exists (else nothing to migrate).
      2. Snapshot the current Kuzu graph counts as the baseline.
      3. Run a full re-index with ``CGH_DB=duckdb`` so the new file is
         populated from the source repo (NOT copied from Kuzu — fresh
         index, so a buggy Kuzu graph is also corrected).
      4. Snapshot the DuckDB counts and diff against the baseline.
      5. On a clean match: optionally delete graph.db. The user is
         prompted unless ``--yes`` or ``--keep-kuzu`` is passed.
      6. On a mismatch: keep both files, print the diff, exit non-zero.
    """
    # NOTE: do NOT .resolve() the root path. On macOS / Linux a symlinked
    # path (e.g. /tmp/x -> /private/tmp/x) would change which absolute path
    # the indexer records in File.path nodes, and the verify step would
    # see "different" rows even though the data is identical. Stick with
    # the path the user typed.
    root = Path(args.root)
    cg = root / _DB_DIR
    kuzu_path = cg / _KUZU_FILE
    duckdb_path = cg / _DUCKDB_FILE

    console.print(LOGO)
    console.print(f"[dim]Repository:[/dim] [bold]{root}[/bold]\n")

    if not kuzu_path.exists():
        console.print(
            Panel(
                "[yellow]No graph.db found in this repo — nothing to migrate.[/yellow]\n"
                "If you wanted to start fresh on DuckDB, just run "
                "[cyan]CGH_DB=duckdb cgh index[/cyan].",
                title="[yellow]Skipped[/yellow]",
                border_style="yellow",
            )
        )
        return

    if duckdb_path.exists() and not args.force:
        console.print(
            Panel(
                f"[yellow]graph.duckdb already exists ({_size_str(duckdb_path.stat().st_size)}).[/yellow]\n"
                "Pass [cyan]--force[/cyan] to overwrite it, or delete it manually first.",
                title="[yellow]Aborted[/yellow]",
                border_style="yellow",
            )
        )
        return

    if duckdb_path.exists() and args.force:
        console.print(f"[dim]Removing existing graph.duckdb ({_size_str(duckdb_path.stat().st_size)})...[/dim]")
        duckdb_path.unlink()

    # Step 1: Kuzu baseline
    console.print("[bold]Step 1[/bold] · Reading current Kuzu graph counts...")
    os.environ.pop("CGH_DB", None)  # auto-detect picks Kuzu since graph.db exists
    kuzu_stats = _stats_snapshot(root)
    kuzu_total_nodes = sum(kuzu_stats["nodes"].values())
    kuzu_total_edges = sum(kuzu_stats["edges"].values())
    console.print(
        f"  [dim]Kuzu graph:[/dim] [bold]{kuzu_total_nodes:,}[/bold] nodes, "
        f"[bold]{kuzu_total_edges:,}[/bold] edges\n"
    )

    # Step 2: Re-index into DuckDB
    console.print("[bold]Step 2[/bold] · Re-indexing into DuckDB...")
    os.environ["CGH_DB"] = "duckdb"
    from codegraph.core.db import reset_connection
    from codegraph.indexer import index_repo

    reset_connection()
    try:
        stats = index_repo(str(root), verbose=False)
    finally:
        # leave CGH_DB set so the post-checks see the duckdb conn
        reset_connection()
    console.print(
        f"  [dim]Indexed:[/dim] {stats.get('indexed', 0)} files, "
        f"{stats.get('errors', 0)} errors, "
        f"elapsed [cyan]{stats.get('elapsed_s', '?')}s[/cyan]\n"
    )

    # Step 3: DuckDB counts + diff
    console.print("[bold]Step 3[/bold] · Verifying DuckDB graph against Kuzu baseline...")
    duckdb_stats = _stats_snapshot(root)
    diffs = _diff_stats(kuzu_stats, duckdb_stats)

    if diffs:
        diff_table = Table(box=box.SIMPLE_HEAD, title="Differing rows", title_style="bold yellow")
        diff_table.add_column("metric", style="bold")
        diff_table.add_column("kuzu", justify="right")
        diff_table.add_column("duckdb", justify="right")
        diff_table.add_column("delta", justify="right", style="yellow")
        for metric, k, d in diffs:
            diff_table.add_row(metric, f"{k:,}", f"{d:,}", f"{d - k:+,}")
        console.print(diff_table)
        console.print(
            Panel(
                "[yellow]Counts differ between Kuzu and DuckDB.[/yellow] "
                "Both files have been kept so you can inspect manually. "
                "Common cause: an interim Kuzu / parser change between when "
                "the Kuzu graph was indexed and now — a re-index of both "
                "backends from the same source should agree.",
                title="[yellow]Mismatch — kept both files[/yellow]",
                border_style="yellow",
            )
        )
        raise SystemExit(1)

    console.print("  [green]+[/green] node + edge counts match exactly.\n")

    # Step 4: optionally drop the old Kuzu graph
    kuzu_size = _size_str(kuzu_path.stat().st_size)
    duckdb_size = _size_str(duckdb_path.stat().st_size)

    summary = Table(box=box.SIMPLE_HEAD, title="Migration summary", title_style="bold cyan")
    summary.add_column("backend", style="bold")
    summary.add_column("file", overflow="fold")
    summary.add_column("size", justify="right")
    summary.add_row("[dim]old[/dim]", "graph.db", kuzu_size)
    summary.add_row("[green]new[/green]", "graph.duckdb", duckdb_size)
    console.print(summary)
    console.print()

    if args.keep_kuzu:
        console.print(
            "[dim]--keep-kuzu was passed — graph.db left in place.[/dim]\n"
            "[dim]Future cgh commands will auto-detect graph.duckdb and use DuckDB.[/dim]"
        )
        return

    if not args.yes:
        try:
            answer = console.input(
                f"Delete the old [bold]graph.db[/bold] ({kuzu_size})? [Y/n] "
            ).strip().lower()
        except EOFError:
            answer = "n"
        if answer in ("n", "no"):
            console.print(
                "\n[dim]Kept graph.db. Future cgh commands will auto-detect graph.duckdb "
                "and use DuckDB anyway.[/dim]"
            )
            return

    kuzu_path.unlink()
    console.print(f"\n[green]Deleted graph.db ({kuzu_size}). Repo is now DuckDB-only.[/green]")
