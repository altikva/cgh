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
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, console

_DB_DIR = ".codegraph"
_KUZU_FILE = "graph.db"
_DUCKDB_FILE = "graph.duckdb"


@dataclass
class MigrationResult:
    """Outcome of one migrate-to-duckdb invocation. Callers (the CLI
    command, cgh init's auto-migration) consume this to decide whether
    to print success, abort, prompt, etc."""

    status: str  # "skipped" | "aborted" | "matched" | "mismatched"
    message: str
    kuzu_nodes: int = 0
    kuzu_edges: int = 0
    duckdb_nodes: int = 0
    duckdb_edges: int = 0
    diffs: list[tuple[str, int, int]] | None = None
    kuzu_deleted: bool = False


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


def do_migrate_to_duckdb(
    root: Path | str,
    *,
    delete_kuzu: bool = True,
    force: bool = False,
) -> MigrationResult:
    """Run the migration. CLI-free entry point — usable from
    cmd_migrate_to_duckdb AND from cmd_init's auto-migration hook.

    NOTE: do NOT .resolve() the root path. On macOS /tmp is a symlink to
    /private/tmp; resolving would change the file_path strings the
    indexer writes, so the verify step would see "different" rows even
    though the data is identical. Stick with what the caller gave us.
    """
    root_p = Path(root)
    cg = root_p / _DB_DIR
    kuzu_path = cg / _KUZU_FILE
    duckdb_path = cg / _DUCKDB_FILE

    if not kuzu_path.exists():
        return MigrationResult(
            status="skipped",
            message="No graph.db found — nothing to migrate.",
        )

    if duckdb_path.exists() and not force:
        return MigrationResult(
            status="aborted",
            message=(
                f"graph.duckdb already exists "
                f"({_size_str(duckdb_path.stat().st_size)}). "
                "Pass force=True to overwrite."
            ),
        )

    if duckdb_path.exists() and force:
        duckdb_path.unlink()

    os.environ.pop("CGH_DB", None)
    kuzu_stats = _stats_snapshot(root_p)
    kuzu_nodes = sum(kuzu_stats["nodes"].values())
    kuzu_edges = sum(kuzu_stats["edges"].values())

    os.environ["CGH_DB"] = "duckdb"
    from codegraph.core.db import reset_connection
    from codegraph.indexer import index_repo

    reset_connection()
    try:
        index_repo(str(root_p), verbose=False)
    finally:
        reset_connection()

    duckdb_stats = _stats_snapshot(root_p)
    diffs = _diff_stats(kuzu_stats, duckdb_stats)
    duckdb_nodes = sum(duckdb_stats["nodes"].values())
    duckdb_edges = sum(duckdb_stats["edges"].values())

    if diffs:
        return MigrationResult(
            status="mismatched",
            message="Counts differ between Kuzu and DuckDB. Both files kept.",
            kuzu_nodes=kuzu_nodes,
            kuzu_edges=kuzu_edges,
            duckdb_nodes=duckdb_nodes,
            duckdb_edges=duckdb_edges,
            diffs=diffs,
        )

    kuzu_deleted = False
    if delete_kuzu and kuzu_path.exists():
        kuzu_path.unlink()
        kuzu_deleted = True

    return MigrationResult(
        status="matched",
        message="Counts match exactly.",
        kuzu_nodes=kuzu_nodes,
        kuzu_edges=kuzu_edges,
        duckdb_nodes=duckdb_nodes,
        duckdb_edges=duckdb_edges,
        kuzu_deleted=kuzu_deleted,
    )


def cmd_migrate_to_duckdb(args) -> None:
    """CLI wrapper — Rich rendering on top of ``do_migrate_to_duckdb``.

    Handles the interactive "delete graph.db?" prompt that's specific to
    the command-line flow. Other callers (cgh init) pass delete_kuzu
    directly.
    """
    root = Path(args.root)
    cg = root / _DB_DIR
    kuzu_path = cg / _KUZU_FILE
    duckdb_path = cg / _DUCKDB_FILE

    console.print(LOGO)
    console.print(f"[dim]Repository:[/dim] [bold]{root}[/bold]\n")

    # Pre-flight: render a friendlier "nothing to do" panel than the bare
    # MigrationResult.message gives us.
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

    # Defer deletion until after the post-match prompt unless --yes
    # was passed (or --keep-kuzu, which means never delete).
    delete_via_function = bool(args.yes and not args.keep_kuzu)

    console.print("[bold]Step 1[/bold] · Reading current Kuzu graph counts...")
    console.print("[bold]Step 2[/bold] · Re-indexing into DuckDB...")
    console.print("[bold]Step 3[/bold] · Verifying DuckDB graph against Kuzu baseline...\n")

    result = do_migrate_to_duckdb(
        args.root, delete_kuzu=delete_via_function, force=args.force
    )

    console.print(
        f"  [dim]Kuzu graph:[/dim] [bold]{result.kuzu_nodes:,}[/bold] nodes, "
        f"[bold]{result.kuzu_edges:,}[/bold] edges"
    )
    console.print(
        f"  [dim]DuckDB:[/dim]    [bold]{result.duckdb_nodes:,}[/bold] nodes, "
        f"[bold]{result.duckdb_edges:,}[/bold] edges\n"
    )

    if result.status == "mismatched":
        diff_table = Table(box=box.SIMPLE_HEAD, title="Differing rows", title_style="bold yellow")
        diff_table.add_column("metric", style="bold")
        diff_table.add_column("kuzu", justify="right")
        diff_table.add_column("duckdb", justify="right")
        diff_table.add_column("delta", justify="right", style="yellow")
        for metric, k, d in result.diffs or []:
            diff_table.add_row(metric, f"{k:,}", f"{d:,}", f"{d - k:+,}")
        console.print(diff_table)
        console.print(
            Panel(
                "[yellow]Counts differ between Kuzu and DuckDB.[/yellow] "
                "Both files have been kept so you can inspect manually.",
                title="[yellow]Mismatch — kept both files[/yellow]",
                border_style="yellow",
            )
        )
        raise SystemExit(1)

    console.print("  [green]+[/green] node + edge counts match exactly.\n")

    kuzu_size = _size_str(kuzu_path.stat().st_size) if kuzu_path.exists() else "(deleted)"
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

    if kuzu_path.exists() and not args.yes:
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

    if not kuzu_path.exists():
        console.print("\n[green]Deleted graph.db. Repo is now DuckDB-only.[/green]")
