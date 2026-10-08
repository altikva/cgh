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
#              preserved. A revised entry supersedes its earlier copy; --archive
#              dumps the whole source store first. Wraps
#              call_log.promote_knowledge.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from codegraph.cli import console


def register_knowledge_parser(sub) -> None:
    kp = sub.add_parser("knowledge", help="Knowledge store maintenance (promote)")
    ksub = kp.add_subparsers(dest="knowledge_cmd")
    pr = ksub.add_parser(
        "promote",
        help="Promote a worktree's learnings into its main checkout at merge",
    )
    pr.add_argument(
        "--from",
        dest="from_",
        default=".",
        help="Source worktree root (default: the current directory)",
    )
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
        help="Comma-separated kinds to promote, exactly (default: the durable "
        "kinds plus plain notes)",
    )
    pr.add_argument(
        "--no-notes",
        action="store_true",
        help="Exclude plain notes from the default kinds",
    )
    pr.add_argument("--pr", default="", help="Provenance: e.g. ondonne-api#123")
    pr.add_argument(
        "--branch", default="", help="Provenance branch (default: --from's git branch)"
    )
    pr.add_argument(
        "--commit", default="", help="Provenance commit (default: --from's git HEAD)"
    )
    pr.add_argument("--session", default="", help="Provenance: the origin session id")
    pr.add_argument(
        "--archive",
        default="",
        metavar="DIR",
        help="First write every knowledge row of the source store to DIR as JSON "
        "lines (digests and superseded rows included)",
    )
    pr.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be promoted; write nothing to the target",
    )
    pr.add_argument("--json", action="store_true", help="Machine-readable output")


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


def _archive(from_root: Path, out_dir: Path, branch: str) -> tuple[Path, int]:
    """Write the source store's whole knowledge table as JSON lines."""
    from codegraph.state.call_log import knowledge_export

    rows = knowledge_export(from_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", branch or from_root.name).strip("-")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"knowledge-{label or 'store'}-{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path, len(rows)


def _fail(msg: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        console.print(f"[red]{msg}[/red]")
    raise SystemExit(1)


def cmd_knowledge(args: argparse.Namespace) -> None:
    if getattr(args, "knowledge_cmd", None) != "promote":
        console.print("[yellow]Usage:[/yellow] cgh knowledge promote --to <checkout>")
        raise SystemExit(2)

    from codegraph.state.call_log import promote_knowledge

    as_json = bool(getattr(args, "json", False))
    from_root = Path(os.path.abspath(args.from_)).resolve()
    to_root = Path(os.path.abspath(args.to)).resolve()
    if not (from_root / ".codegraph" / "call_log.db").exists():
        _fail(f"No knowledge store under {from_root}", as_json)
    if not (to_root / ".codegraph").exists():
        _fail(f"No .codegraph store under {to_root}", as_json)
    if from_root == to_root:
        _fail("--from and --to are the same checkout", as_json)

    branch = args.branch or _git(from_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = args.commit or _git(from_root, "rev-parse", "--short", "HEAD")
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None

    archived: dict | None = None
    if getattr(args, "archive", ""):
        path, n = _archive(from_root, Path(os.path.abspath(args.archive)), branch)
        archived = {"path": str(path), "rows": n}

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
        source_worktree=str(from_root),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if as_json:
        print(
            json.dumps(
                {
                    **result,
                    "from": str(from_root),
                    "to": str(to_root),
                    "branch": branch,
                    "commit": commit,
                    "pr": args.pr or None,
                    "archive": archived,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if archived:
        console.print(f"Archived {archived['rows']} row(s) to {archived['path']}")
    verb = "Would promote" if result["dry_run"] else "Promoted"
    console.print(
        f"[green]{verb}[/green] {result['promoted']} entr(y/ies) into {to_root.name} "
        f"from {branch or from_root.name}: {result['copied']} new, "
        f"{result['superseded']} superseding an earlier copy, "
        f"{result['skipped_duplicate']} already present "
        f"({result['considered']} considered)."
    )
