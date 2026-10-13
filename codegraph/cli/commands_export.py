# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh export --format graphml [--out FILE] [--scope PATH]`:
#              writes the code graph as a file other graph tools open
#              (viz/graphml.py). Reads the graph DB read-only, on either
#              backend; stdout when no --out is given, so it pipes.

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

FORMATS = ("graphml",)


def register_export_parser(sub) -> None:
    p = sub.add_parser(
        "export",
        help="Export the code graph to a file (GraphML for Gephi / yEd)",
    )
    p.add_argument(
        "--format",
        choices=list(FORMATS),
        default="graphml",
        help="Export format (default: graphml)",
    )
    p.add_argument(
        "--out",
        metavar="PATH",
        default="",
        help="Write the export to this file (default: stdout)",
    )
    p.add_argument(
        "--scope",
        default="",
        metavar="PATH",
        help="Only the files under this repo-relative directory or file, "
        "and the edges between them",
    )
    p.add_argument("--root", default=os.getcwd())


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def cmd_export(args: argparse.Namespace) -> None:
    """Handler for `cgh export`."""
    from rich.console import Console

    from codegraph.cli import LOGO, _query_conn
    from codegraph.viz.graphml import write_graphml

    err = Console(stderr=True)
    err.print(LOGO)
    root = os.path.abspath(args.root)
    if not (Path(root) / ".codegraph").is_dir():
        _fail(
            "repo is not indexed by cgh (.codegraph/ missing). "
            "Run `cgh init` then `cgh index`."
        )
    conn = _query_conn(root)
    if conn is None:
        _fail(
            "The graph DB is locked by a running cgh owner (or missing). "
            f"Stop the owner with `cgh stop --root {root}` and retry, or run "
            "`cgh index` if the repo was never indexed."
        )

    scope = getattr(args, "scope", "") or ""
    out = getattr(args, "out", "") or ""
    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written to a buffer first so a failure leaves no half-written file.
        buf = io.StringIO()
        counts = write_graphml(conn, root, buf, scope=scope)
        target.write_text(buf.getvalue(), encoding="utf-8")
        err.print(
            f"[green]+[/green] {target} "
            f"[dim]({counts['nodes']:,} nodes, {counts['edges']:,} edges)[/dim]"
        )
    else:
        counts = write_graphml(conn, root, sys.stdout, scope=scope)
        sys.stdout.flush()
    if counts["nodes"] == 0:
        hint = f" under scope '{counts['scope']}'" if counts["scope"] else ""
        err.print(f"[yellow]The graph has no nodes{hint}.[/yellow]")
