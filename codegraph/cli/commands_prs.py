# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh prs overlap`: which open pull requests of this repo touch
#              the same symbols, call into each other's changes, or edit the
#              same files. Reads the PRs through the GitHub CLI and maps their
#              diffs onto the graph (analysis/pr_overlap.py). Asks the repo's
#              live owner when one holds the graph (the pr_overlap MCP tool),
#              else opens the graph DB read-only. Emits markdown or JSON.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from codegraph.cli.output import add_out_option

ACTIONS = ("overlap",)
# Fetching many PR diffs takes a while: give the owner call room.
_OWNER_TIMEOUT_FLOOR = 300.0


def register_prs_parser(sub) -> None:
    p = sub.add_parser(
        "prs",
        help="Open pull requests: overlap between them (reads PRs via gh)",
    )
    p.add_argument("action", choices=list(ACTIONS), help="overlap")
    p.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Open PRs to read, newest first (default: 30, max: 100)",
    )
    p.add_argument(
        "--json", action="store_true", help="Emit JSON (shorthand for --format json)"
    )
    p.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="Output format (default: md)",
    )
    add_out_option(p, what="the report")
    p.add_argument("--root", default=os.getcwd())


def _fail(want_json: bool, message: str) -> None:
    """A clean failure: an {"error"} object in JSON mode, a note on stderr
    otherwise, exit 1. Never a traceback."""
    if want_json:
        print(json.dumps({"error": message}, indent=2))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def cmd_prs(args: argparse.Namespace) -> None:
    """Handler for `cgh prs overlap`."""
    from rich.console import Console

    from codegraph.cli import LOGO

    root = os.path.abspath(args.root)
    want_json = bool(getattr(args, "json", False)) or args.format == "json"
    Console(stderr=True).print(LOGO)

    if not (Path(root) / ".codegraph").is_dir():
        _fail(
            want_json,
            "repo is not indexed by cgh (.codegraph/ missing). "
            "Run `cgh init` then `cgh index`.",
        )
    report = _overlap(root, int(args.limit), want_json)
    if "error" in report:
        _fail(want_json, str(report["error"]))

    from codegraph.analysis.pr_overlap import render_markdown
    from codegraph.cli.output import emit_result

    out = getattr(args, "out", "")
    if want_json:
        emit_result(json.dumps(report, indent=2), out=out, hint="overlap.json")
    else:
        emit_result(render_markdown(report), out=out, hint="overlap.md")


def _overlap(root: str, limit: int, want_json: bool) -> dict:
    """The report from a live owner, else from a local read-only open."""
    from codegraph.cli.owner_client import (
        call_owner_tool,
        note_route,
        older_owner_hint,
        owner_timeout,
        stuck_owner_hint,
    )

    reply = call_owner_tool(
        root,
        "pr_overlap",
        {"limit": limit},
        timeout=max(owner_timeout(), _OWNER_TIMEOUT_FLOOR),
    )
    if reply.ok and isinstance(reply.data, dict):
        note_route("prs overlap", "owner")
        return reply.data
    if reply.status == "timeout":
        return {"error": stuck_owner_hint(root, reply)}
    if reply.status == "unknown_tool":
        return {"error": older_owner_hint(root, "pr_overlap")}

    from codegraph.analysis.pr_overlap import fetch_open_prs, overlap_report
    from codegraph.core.db import get_readonly_connection
    from codegraph.state.scan_meta import outdated_store_message

    # Read the PRs first: when gh cannot be used, say so before touching
    # the graph.
    prs, warnings, err = fetch_open_prs(root, limit)
    if err is not None:
        return {"error": err}
    outdated = outdated_store_message(root)
    if outdated:
        return {"error": outdated}
    try:
        conn = get_readonly_connection(root)
    except Exception as exc:
        return {"error": f"could not open graph read-only: {exc}"}
    if conn is None:
        if reply.status == "error":
            return {"error": stuck_owner_hint(root, reply)}
        return {
            "error": "graph DB is missing or locked by another cgh process. "
            "Run `cgh index` if the repo was never indexed."
        }
    note_route("prs overlap", "local read-only open")
    report = overlap_report(conn, root, prs)
    if warnings:
        report["warnings"] = warnings
    return report
