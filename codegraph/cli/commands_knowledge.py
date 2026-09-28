# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh knowledge promote`: carry durable learnings from a per-ticket
#              worktree's store into its main checkout at merge, so a torn-down
#              worktree does not take its knowledge with it. Only repo-scoped
#              real learnings move (session digests and auto-checkpoints never
#              do), deduplicated on exact title+body against the target, with
#              origin timestamp and provenance (branch, commit, PR, session)
#              preserved. Wraps call_log.promote_knowledge.

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from codegraph.cli import console


def register_knowledge_parser(sub) -> None:
    kp = sub.add_parser("knowledge", help="Knowledge store maintenance (promote)")
    ksub = kp.add_subparsers(dest="knowledge_cmd")
    pr = ksub.add_parser(
        "promote",
        help="Promote a worktree's learnings into its main checkout at merge",
    )
    pr.add_argument("--from", dest="from_", required=True, help="Source worktree root")
    pr.add_argument("--to", dest="to", required=True, help="Target checkout root")
    pr.add_argument(
        "--since",
        type=float,
        default=0.0,
        help="Only entries with ts >= this epoch value (default: all)",
    )
    pr.add_argument(
        "--kinds",
        default="",
        help="Comma-separated kinds to promote (default: the durable kinds)",
    )
    pr.add_argument(
        "--no-notes",
        action="store_true",
        help="Exclude plain notes; promote only the explicit kinds",
    )
    pr.add_argument("--pr", default="", help="Provenance: e.g. ondonne-api#123")
    pr.add_argument(
        "--branch", default="", help="Provenance branch (default: --from's git branch)"
    )
    pr.add_argument(
        "--commit", default="", help="Provenance commit (default: --from's git HEAD)"
    )
    pr.add_argument("--session", default="", help="Provenance: the origin session id")


def _git(root: Path, *args: str) -> str:
    """Best-effort git read against ``root``; empty string on any failure."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def cmd_knowledge(args: argparse.Namespace) -> None:
    if getattr(args, "knowledge_cmd", None) != "promote":
        console.print(
            "[yellow]Usage:[/yellow] cgh knowledge promote --from <wt> --to <checkout>"
        )
        raise SystemExit(2)

    from codegraph.state.call_log import promote_knowledge

    from_root = Path(os.path.abspath(args.from_))
    to_root = Path(os.path.abspath(args.to))
    if not (from_root / ".codegraph").exists():
        console.print(f"[red]No .codegraph store under[/red] {from_root}")
        raise SystemExit(1)
    if not (to_root / ".codegraph").exists():
        console.print(f"[red]No .codegraph store under[/red] {to_root}")
        raise SystemExit(1)

    branch = args.branch or _git(from_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = args.commit or _git(from_root, "rev-parse", "--short", "HEAD")
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None

    result = promote_knowledge(
        from_root,
        to_root,
        since_ts=args.since,
        kinds=kinds,
        include_plain_notes=not args.no_notes,
        source_branch=branch,
        source_commit=commit,
        source_pr=args.pr,
        source_session=args.session,
    )
    console.print(
        f"[green]Promoted[/green] {result['promoted']} entr(y/ies) into {to_root.name} "
        f"({result['skipped_duplicate']} already present, "
        f"{result['considered']} considered)."
    )
