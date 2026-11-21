# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh knowledge promote`: carry matured learnings from this repo's
#              store into a shared store read across the fleet (the default
#              target, a namespace under ~/.cgh/shared), or into a main checkout
#              at merge when --to names a path (the worktree->main case, so a
#              torn-down worktree does not take its knowledge with it). Only
#              repo-scoped real learnings move (session digests and
#              auto-checkpoints never do), deduplicated on exact title+body
#              against the target, with origin timestamp and provenance (repo,
#              ticket, author, branch, commit, PR, session) preserved. A shared
#              target refuses standing_instruction/glossary. A revised entry
#              supersedes its earlier copy; --archive dumps the whole source
#              store first. Wraps call_log.promote_knowledge.
#              `cgh knowledge outcome` records whether relying on an entry or
#              a file worked (useful, dead_end, corrected); `cgh knowledge
#              outcomes` shows the derived standing of rated entries and files.
#              `cgh knowledge export` / `import` move a filtered store between
#              checkouts as JSON Lines (wraps state/knowledge_transfer).

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from cgh.cli import console
from cgh.core.config import state_dir_for


def register_knowledge_parser(sub) -> None:
    kp = sub.add_parser(
        "knowledge",
        help="Knowledge store maintenance (promote, outcomes, export, import)",
    )
    ksub = kp.add_subparsers(dest="knowledge_cmd")
    pr = ksub.add_parser(
        "promote",
        help="Promote matured learnings into a shared store (or a main checkout)",
    )
    pr.add_argument(
        "--from",
        dest="from_",
        default=".",
        help="Source repo/worktree root (default: the current directory)",
    )
    pr.add_argument(
        "--to",
        dest="to",
        default="",
        help="Target: a shared namespace read across the fleet (default: the "
        "repo's knowledge_promote_default, else org-platform), or a checkout "
        "path for a worktree->main promotion",
    )
    pr.add_argument(
        "--since",
        default="",
        help="Only entries at or after this time (epoch or ISO date; default: all)",
    )
    pr.add_argument(
        "--tags",
        default="",
        help="Comma-separated; promote only entries carrying at least one of them",
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

    oc = ksub.add_parser(
        "outcome", help="Record whether relying on an entry or a file worked"
    )
    oc.add_argument("verdict", choices=["useful", "dead_end", "corrected"])
    oc.add_argument("--id", dest="entry_id", type=int, default=0, help="Entry id")
    oc.add_argument(
        "--query", default="", help="Target the best search hit instead of an id"
    )
    oc.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        help="A file or symbol the verdict covers (repeatable)",
    )
    oc.add_argument("--note", default="", help="One line on why")
    oc.add_argument(
        "--corrected-by",
        dest="corrected_by",
        type=int,
        default=0,
        help="With corrected: the id of the entry that fixed it",
    )
    oc.add_argument("--json", action="store_true", help="Machine-readable output")

    ol = ksub.add_parser("outcomes", help="Show rated entries and files")
    ol.add_argument("--limit", type=int, default=20, help="Rows per section")
    ol.add_argument("--json", action="store_true", help="Machine-readable output")

    ex = ksub.add_parser(
        "export", help="Write the knowledge store as JSON Lines, optionally filtered"
    )
    ex.add_argument("--root", default=os.getcwd(), help="Store to read")
    when = ex.add_mutually_exclusive_group()
    when.add_argument(
        "--before",
        default="",
        metavar="TIME",
        help="Only entries with ts strictly before TIME (epoch or ISO date, UTC "
        "when no zone)",
    )
    when.add_argument(
        "--before-commit",
        dest="before_commit",
        default="",
        metavar="REV",
        help="Like --before, at the committer date of REV resolved in --root",
    )
    ex.add_argument(
        "--exclude-tag",
        dest="exclude_tags",
        action="append",
        default=[],
        metavar="GLOB",
        help="Drop entries with a tag matching GLOB (repeatable). Provenance "
        "matches too, as pr:<source_pr>, branch:<source_branch>, "
        "commit:<source_commit> and session:<source_session>",
    )
    ex.add_argument(
        "--kinds", default="", help="Comma-separated kinds to keep (default: all)"
    )
    ex.add_argument(
        "--include-digests",
        dest="include_digests",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep session digests and checkpoints (default: yes; the other "
        "filters apply to them either way)",
    )

    im = ksub.add_parser(
        "import", help="Load a `cgh knowledge export` JSON Lines file into a store"
    )
    im.add_argument("--root", default=os.getcwd(), help="Store to write")
    im.add_argument(
        "file", nargs="?", default="-", help="JSON Lines file, or - for stdin"
    )
    im.add_argument("--json", action="store_true", help="Machine-readable output")

    ml = ksub.add_parser(
        "merge-legacy",
        help="Fold a leftover .codegraph/ knowledge store into .cgh/ (idempotent)",
    )
    ml.add_argument("--root", default=os.getcwd(), help="Repo holding both dirs")
    ml.add_argument(
        "--force",
        action="store_true",
        help="Merge again even if it already ran (duplicates are still skipped)",
    )
    ml.add_argument("--json", action="store_true", help="Machine-readable output")


def _cmd_merge_legacy(args: argparse.Namespace) -> None:
    from cgh.state.knowledge_transfer import (
        KnowledgeTransferError,
        merge_legacy_knowledge,
    )

    root = Path(os.path.abspath(args.root)).resolve()
    try:
        counts = merge_legacy_knowledge(root, force=args.force)
    except (KnowledgeTransferError, OSError) as exc:
        _err(f"cgh knowledge merge-legacy: {exc}")
        return
    if args.json:
        print(
            json.dumps(
                {"merged": counts is not None, **(counts or {}), "root": str(root)}
            )
        )
        return
    if counts is None:
        console.print(
            f"Nothing to merge in {root.name} (no .cgh/ and .codegraph/ pair, "
            "or already merged; --force to redo)."
        )
        return
    console.print(
        f"[green]Merged[/green] {counts['imported']} legacy entr(y/ies) into {root.name}, "
        f"{counts['skipped_duplicate']} already present; "
        f"{counts['outcomes_imported']} outcome(s) imported."
    )


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
    from cgh.state.call_log import knowledge_export

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


def _store_root(as_json: bool) -> Path:
    from cgh.core.config import find_cgh_root

    root = find_cgh_root(Path.cwd())
    if root is None:
        _fail("Not inside a cgh project (no .cgh/ found)", as_json)
    return root  # type: ignore[return-value]


def _cmd_outcome(args: argparse.Namespace) -> None:
    from cgh.state.call_log import (
        knowledge_outcome_record,
        knowledge_outcome_scores,
        knowledge_search,
    )

    as_json = bool(args.json)
    root = _store_root(as_json)
    entry_id = int(args.entry_id or 0)
    if not entry_id and args.query.strip():
        hits = knowledge_search(
            args.query, limit=1, repo_root=root, rank_outcomes=False
        )
        if not hits:
            _fail(f"No knowledge entry matches {args.query!r}", as_json)
        entry_id = int(hits[0]["id"])
    try:
        outcome_id = knowledge_outcome_record(
            verdict=args.verdict,
            entry_id=entry_id or None,
            file_refs=args.files,
            note=args.note,
            corrected_by=args.corrected_by or None,
            repo_root=root,
        )
    except ValueError as exc:
        _fail(str(exc), as_json)
        return
    entries, _files = knowledge_outcome_scores(repo_root=root)
    standing = entries[entry_id].summary() if entry_id in entries else None
    if as_json:
        print(
            json.dumps(
                {
                    "id": outcome_id,
                    "verdict": args.verdict,
                    "entry_id": entry_id or None,
                    "files": args.files,
                    "outcome": standing,
                }
            )
        )
        return
    target = f"entry {entry_id}" if entry_id else ", ".join(args.files)
    line = f"[green]Recorded[/green] {args.verdict} for {target}"
    if standing:
        line += f" (now {standing['status']}, score {standing['score']})"
    console.print(line)


def _cmd_outcomes(args: argparse.Namespace) -> None:
    from cgh.state.call_log import knowledge_export, knowledge_outcome_scores

    as_json = bool(args.json)
    root = _store_root(as_json)
    entries, files = knowledge_outcome_scores(repo_root=root)
    titles = {r["id"]: r["title"] for r in knowledge_export(root)}

    def _order(item):
        return (-abs(item[1].score), -item[1].last_ts)

    entry_rows = [
        {"entry_id": i, "title": titles.get(i, ""), **s.summary()}
        for i, s in sorted(entries.items(), key=_order)
    ][: args.limit]
    file_rows = [
        {"file": f, **s.summary()} for f, s in sorted(files.items(), key=_order)
    ][: args.limit]
    if as_json:
        print(json.dumps({"entries": entry_rows, "files": file_rows}, indent=2))
        return
    if not entry_rows and not file_rows:
        console.print("No outcomes recorded yet.")
        return
    for label, rows, key in (
        ("Entries", entry_rows, "entry_id"),
        ("Files", file_rows, "file"),
    ):
        if not rows:
            continue
        console.print(f"[bold]{label}[/bold]")
        for r in rows:
            name = (
                f"#{r[key]} {r.get('title', '')}".strip()
                if key == "entry_id"
                else r[key]
            )
            console.print(
                f"  {r['status']:<10} {r['score']:>7.3f}  "
                f"+{r['useful']} -{r['dead_end']} x{r['corrected']}  {name}"
            )


def _err(msg: str) -> None:
    import sys

    print(msg, file=sys.stderr)
    raise SystemExit(1)


def _cmd_export(args: argparse.Namespace) -> None:
    import sys

    from cgh.state.knowledge_transfer import (
        KnowledgeTransferError,
        commit_time,
        export_records,
        parse_time,
    )

    root = Path(os.path.abspath(args.root)).resolve()
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None
    try:
        before = None
        if args.before_commit:
            before = commit_time(root, args.before_commit)
        elif args.before:
            before = parse_time(args.before)
        records = export_records(
            root,
            before=before,
            exclude_tags=args.exclude_tags,
            kinds=kinds,
            include_digests=args.include_digests,
        )
    except KnowledgeTransferError as exc:
        _err(f"cgh knowledge export: {exc}")
        return
    out = sys.stdout
    for rec in records:
        out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    out.flush()


def _cmd_import(args: argparse.Namespace) -> None:
    import sys

    from cgh.state.ipc import is_owner_alive, read_owner_pid
    from cgh.state.knowledge_transfer import (
        KnowledgeTransferError,
        import_records,
    )

    root = Path(os.path.abspath(args.root)).resolve()
    if not (state_dir_for(root)).is_dir():
        _err(f"cgh knowledge import: no .cgh store under {root} (run cgh init)")
    if is_owner_alive(root):
        _err(
            f"cgh knowledge import: a cgh owner is running on {root} "
            f"(pid {read_owner_pid(root)}) and holds its store.\n"
            f"Stop it first, then re-run: cgh stop --root {root}"
        )

    def _lines():
        fh = (
            sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")  # noqa: SIM115
        )
        try:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise KnowledgeTransferError(f"line {n}: {exc}") from exc
        finally:
            if fh is not sys.stdin:
                fh.close()

    try:
        counts = import_records(root, _lines())
    except (KnowledgeTransferError, OSError) as exc:
        _err(f"cgh knowledge import: {exc}")
        return
    if args.json:
        print(json.dumps({**counts, "root": str(root)}))
        return
    console.print(
        f"[green]Imported[/green] {counts['imported']} entr(y/ies) into {root.name}, "
        f"{counts['skipped_duplicate']} already present; "
        f"{counts['outcomes_imported']} outcome(s) imported, "
        f"{counts['outcomes_skipped']} skipped."
    )


def cmd_knowledge(args: argparse.Namespace) -> None:
    sub = getattr(args, "knowledge_cmd", None)
    if sub == "export":
        _cmd_export(args)
        return
    if sub == "import":
        _cmd_import(args)
        return
    if sub == "outcome":
        _cmd_outcome(args)
        return
    if sub == "outcomes":
        _cmd_outcomes(args)
        return
    if sub == "merge-legacy":
        _cmd_merge_legacy(args)
        return
    if sub != "promote":
        console.print(
            "[yellow]Usage:[/yellow] cgh knowledge promote [--to <namespace|checkout>] "
            "[--since T] [--tags ...] [--dry-run] | "
            "outcome <verdict> [--id N | --query Q] [--file F] | outcomes | "
            "export [--before-commit REV] [--exclude-tag GLOB] | import [FILE] | "
            "merge-legacy [--root R]"
        )
        raise SystemExit(2)

    from cgh.state.call_log import is_shared_namespace, promote_knowledge, repo_slug

    as_json = bool(getattr(args, "json", False))
    from_root = Path(os.path.abspath(args.from_)).resolve()
    if not (state_dir_for(from_root) / "call_log.db").exists():
        _fail(f"No knowledge store under {from_root}", as_json)

    since_ts = 0.0
    since_val = str(getattr(args, "since", "") or "").strip()
    if since_val:
        from cgh.state.knowledge_transfer import KnowledgeTransferError, parse_time

        try:
            since_ts = parse_time(since_val)
        except KnowledgeTransferError as exc:
            _fail(f"--since: {exc}", as_json)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None
    tags_val = str(getattr(args, "tags", "") or "")
    tag_list = [t.strip() for t in tags_val.split(",") if t.strip()] or None
    branch = args.branch or _git(from_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = args.commit or _git(from_root, "rev-parse", "--short", "HEAD")

    # A --to that names a filesystem path promotes into that checkout (the
    # worktree->main case); anything else is a shared namespace, the default.
    to_arg = (args.to or "").strip()
    sep_hit = os.sep in to_arg or bool(os.altsep and os.altsep in to_arg)
    is_checkout = bool(to_arg) and (sep_hit or Path(to_arg).is_dir())

    archived: dict | None = None
    if getattr(args, "archive", ""):
        path, n = _archive(from_root, Path(os.path.abspath(args.archive)), branch)
        archived = {"path": str(path), "rows": n}

    if is_checkout:
        to_root = Path(os.path.abspath(to_arg)).resolve()
        if not (state_dir_for(to_root)).exists():
            _fail(f"No .cgh store under {to_root}", as_json)
        if from_root == to_root:
            _fail("--from and --to are the same checkout", as_json)
        result = promote_knowledge(
            from_root,
            to_root,
            since_ts=since_ts,
            kinds=kinds,
            tags_any=tag_list,
            include_plain_notes=not args.no_notes,
            source_branch=branch,
            source_commit=commit,
            source_pr=args.pr,
            source_session=args.session,
            source_worktree=str(from_root),
            origin_repo=repo_slug(from_root),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        target_label = to_root.name
        target_key: dict = {"to": str(to_root)}
    else:
        from cgh.core.config import load_config

        namespace = to_arg
        if not namespace:
            try:
                namespace = load_config(from_root).knowledge_promote_default
            except Exception:
                namespace = "org-platform"
            namespace = namespace or "org-platform"
        if not is_shared_namespace(namespace):
            _fail(f"{namespace!r} is not a valid shared namespace", as_json)
        result = promote_knowledge(
            from_root,
            to_namespace=namespace,
            since_ts=since_ts,
            kinds=kinds,
            tags_any=tag_list,
            include_plain_notes=not args.no_notes,
            source_branch=branch,
            source_commit=commit,
            source_pr=args.pr,
            source_session=args.session,
            source_worktree=str(from_root),
            origin_repo=repo_slug(from_root),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        target_label = namespace
        target_key = {"to_namespace": namespace}

    if as_json:
        print(
            json.dumps(
                {
                    **result,
                    "from": str(from_root),
                    **target_key,
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
    line = (
        f"[green]{verb}[/green] {result['promoted']} entr(y/ies) into {target_label} "
        f"from {branch or from_root.name}: {result['copied']} new, "
        f"{result['superseded']} superseding an earlier copy, "
        f"{result['skipped_duplicate']} already present "
        f"({result['considered']} considered)."
    )
    if result.get("refused_shared_kind"):
        line += (
            f" {result['refused_shared_kind']} standing_instruction/glossary "
            f"not shared."
        )
    console.print(line)
