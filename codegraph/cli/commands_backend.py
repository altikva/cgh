# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh backend` shows the active graph backend and switches it.
#              SQLite is the light default (the standalone binary ships it
#              alone); DuckDB is faster on heavy analytical queries. Switching
#              is a reindex, not a data migration, since the graph is derived
#              from source: it rebuilds into the target backend's file and
#              drops the old one. When DuckDB is not installed (the SQLite-only
#              binary), it prints the one-line upgrade instead of failing.

from __future__ import annotations

import argparse
import os
from pathlib import Path

from codegraph.cli import console

# A graph this size on SQLite is where DuckDB's columnar engine starts to win
# on the analytical queries (dead-code sweeps, aggregations). Below it, SQLite
# is as fast or faster and far lighter, so no suggestion is made.
_SUGGEST_NODES = 50_000

# uvx fetches its own standalone CPython, so this brings DuckDB even to a host
# with no Python installed (where the SQLite-only binary runs today).
_UPGRADE_HINT = "uvx cgh serve   # the pip/uvx build bundles DuckDB"


def register_backend_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("backend", help="Show or switch the graph backend")
    p.add_argument(
        "target",
        nargs="?",
        choices=["sqlite", "duckdb"],
        help="Switch to this backend (reindexes). Omit to show the current one.",
    )
    p.add_argument("--root", default=os.getcwd())
    p.add_argument("--yes", action="store_true", help="Skip the switch confirmation")


def _node_total(root: Path) -> int | None:
    from codegraph.core.db import get_readonly_connection, reset_connection
    from codegraph.core.graph_model import NODES

    conn = get_readonly_connection(root)
    if conn is None:
        return None
    try:
        return sum(conn.count_nodes(lbl) for lbl in NODES)
    except Exception:
        return None
    finally:
        reset_connection(root)


def cmd_backend(args: argparse.Namespace) -> None:
    from codegraph.core.db import (
        _DB_DIR,
        _DUCKDB_FILE,
        _SQLITE_FILE,
        _backend,
        detect_backend_file,
        duckdb_available,
        get_connection,
        reset_connection,
    )
    from codegraph.indexer import index_repo

    root = Path(os.path.abspath(args.root))
    detected = detect_backend_file(root)
    current = detected[0] if detected else _backend(root)
    available = ["sqlite"] + (["duckdb"] if duckdb_available() else [])

    # --- show ---
    if not args.target:
        console.print(f"[bold]backend:[/bold] {current}")
        console.print(f"[dim]available: {', '.join(available)}[/dim]")
        if current == "sqlite":
            n = _node_total(root)
            if n is not None and n >= _SUGGEST_NODES:
                console.print(
                    f"[yellow]this graph has ~{n:,} nodes.[/yellow] DuckDB is faster "
                    "on heavy analytical queries at this size."
                )
                if duckdb_available():
                    console.print("  switch with: [bold]cgh backend duckdb[/bold]")
                else:
                    console.print(
                        f"  DuckDB is not in this build. Get it with: [bold]{_UPGRADE_HINT}[/bold]"
                    )
        return

    # --- switch ---
    if args.target == current:
        console.print(f"[dim]already on {current}.[/dim]")
        return
    if args.target == "duckdb" and not duckdb_available():
        console.print(
            "[yellow]DuckDB is not available in this build.[/yellow] "
            f"Get the DuckDB-capable cgh with:\n  [bold]{_UPGRADE_HINT}[/bold]\n"
            "then run [bold]cgh backend duckdb[/bold] there."
        )
        raise SystemExit(1)

    if not args.yes:
        console.print(
            f"Switch backend {current} -> {args.target}? This reindexes the repo "
            "(minutes on a large one) and removes the old graph file."
        )
        if console.input("[yellow]Continue? [y/N][/yellow] ").strip().lower() not in (
            "y",
            "yes",
        ):
            console.print("[dim]Aborted.[/dim]")
            return

    # Reindex into the target backend, then drop the previous backend's file so
    # on-disk detection resolves to the new one from now on.
    os.environ["CGH_DB"] = args.target
    reset_connection(root)
    get_connection(root)  # creates the target DB file + schema
    reset_connection(root)
    with console.status(f"[bold]Reindexing into {args.target}...", spinner="dots"):
        index_repo(str(root))
    reset_connection(root)

    old_file = _DUCKDB_FILE if args.target == "sqlite" else _SQLITE_FILE
    (root / _DB_DIR / old_file).unlink(missing_ok=True)
    console.print(f"[green]switched to {args.target}[/green] and reindexed.")
