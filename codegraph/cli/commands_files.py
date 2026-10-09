# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh files` verb: list the indexed files (optionally filtered
#              by a path substring), and `--check <path>` answers "is this
#              file indexed, and if not, why was it skipped" using the same
#              decision the indexer makes (no parser for the suffix, over the
#              size cap, or excluded by an ignore rule). The why-skipped
#              answer is a pure function of the file and config, so it works
#              even while an owner holds the graph write lock. The index
#              itself is read through a live owner's indexed_files tool when
#              one runs, and through a local read-only open otherwise.

from __future__ import annotations

import argparse
import os
from pathlib import Path

from codegraph.cli import _query_conn, _short_path, console


def register_files_parser(sub) -> None:
    p = sub.add_parser(
        "files",
        help="List indexed files, or check whether one is indexed (--check)",
    )
    p.add_argument("--root", default=".")
    p.add_argument(
        "pattern",
        nargs="?",
        default="",
        help="Only list indexed files whose path contains this substring",
    )
    p.add_argument(
        "--check",
        metavar="PATH",
        default="",
        help="Report whether PATH is indexed, and if not, why it was skipped",
    )
    p.add_argument("--limit", type=int, default=200, help="Max files to list")
    p.set_defaults(func=cmd_files)


def _index_decision(path: Path, root: Path) -> tuple[bool, str]:
    """Would the indexer take this file? Mirrors index_file's skip checks
    (no parser, ignore rules, size cap), so the answer explains a skip
    without needing the graph. Returns (indexable, reason)."""
    from codegraph.core.config import load_config
    from codegraph.parsers import get_parser_for_path

    if not path.exists():
        return False, "the file does not exist"
    if path.is_dir():
        return False, "it is a directory, not a file"
    if get_parser_for_path(path) is None:
        return (
            False,
            f"no parser claims '{path.suffix or 'no suffix'}' (so it is skipped)",
        )

    cfg = load_config(root)
    import fnmatch

    if any(fnmatch.fnmatch(path.name, pat) for pat in cfg.ignore_patterns):
        return False, "it matches an [codegraph] ignore_patterns entry"
    try:
        size_kb = path.stat().st_size / 1024
        if size_kb > cfg.max_file_size_kb:
            return (
                False,
                f"it is {size_kb:.0f} KB, over the max_file_size_kb cap of "
                f"{cfg.max_file_size_kb} KB (raise it in .codegraph/config.toml)",
            )
    except OSError:
        pass
    return True, "it is indexable (run `cgh index` if it is still missing)"


def _say_stuck(msg: str) -> None:
    console.print(f"[yellow]{msg}[/yellow]")


def _ask_owner(root: Path, arguments: dict) -> tuple[str, dict | None]:
    """Ask the live owner's indexed_files tool first: it holds the graph DB
    for writing, which blocks our own read-only open. Returns the route
    ("owner", "older" or "local") and the owner's answer. Never starts an
    owner; a timed-out owner exits 1 with the remedy."""
    from codegraph.cli.owner_client import note_route, route_owner_read

    route, data = route_owner_read(
        str(root), "files", "indexed_files", arguments, _say_stuck
    )
    if route == "older":
        # An owner from an older cgh has no indexed_files tool but holds the
        # graph lock: answer from the FTS at once instead of waiting on it.
        note_route("files", "FTS (older owner)")
    return route, data


def _is_indexed(root: Path, abspath: str) -> bool | None:
    """True/False if we can read the index, None if the graph is locked by
    a running owner and the FTS has no record either."""
    route, served = _ask_owner(root, {"path": abspath})
    if route == "owner" and "indexed" in served:
        return bool(served["indexed"])
    conn = _query_conn(str(root)) if route == "local" else None
    if conn is not None:
        try:
            return conn.query_node_field("File", "path", abspath, "path") is not None
        except Exception:
            pass
    # Graph locked or unreadable: fall back to the FTS (SQLite, concurrent
    # readers). A file with indexed symbols or sections shows up here.
    try:
        from codegraph.core.fts import get_fts_conn

        fts = get_fts_conn(str(root))
        row = fts.execute(
            "SELECT 1 FROM symbols WHERE file_path = ? LIMIT 1", (abspath,)
        ).fetchone()
        return bool(row)
    except Exception:
        return None


def _check(root: Path, target: str) -> None:
    abspath = str(
        (root / target).resolve() if not os.path.isabs(target) else Path(target)
    )
    indexed = _is_indexed(root, abspath)
    rel = _short_path(abspath, str(root))
    if indexed is True:
        console.print(f"[green]indexed[/green]  {rel}")
        return
    ok, reason = _index_decision(Path(abspath), root)
    if indexed is None:
        console.print(
            f"[yellow]unknown[/yellow] (graph locked by a running owner)  {rel}"
        )
    else:
        console.print(f"[red]not indexed[/red]  {rel}")
    verdict = "would be indexed" if ok else "skipped"
    console.print(f"  {verdict}: {reason}")


def _list(root: Path, pattern: str, limit: int) -> None:
    rows: list[str] = []
    source = "graph"
    route, served = _ask_owner(root, {"pattern": pattern, "limit": limit})
    if route == "owner" and "total" in served:
        _print_list(root, served.get("files") or [], served["total"], limit, source)
        return
    conn = _query_conn(str(root)) if route == "local" else None
    if conn is not None:
        try:
            contains = {"path": pattern} if pattern else None
            found = conn.find_nodes(
                "File", contains=contains, return_fields=["path"], order_by=["path"]
            )
            rows = [r["path"] for r in found]
        except Exception:
            conn = None
    if conn is None:
        source = "FTS (owner holds the graph lock; symbol-less files may be missing)"
        try:
            from codegraph.core.fts import get_fts_conn

            fts = get_fts_conn(str(root))
            sql = "SELECT DISTINCT file_path FROM symbols"
            params: tuple = ()
            if pattern:
                sql += " WHERE file_path LIKE ?"
                params = (f"%{pattern}%",)
            sql += " ORDER BY file_path"
            rows = [r[0] for r in fts.execute(sql, params).fetchall()]
        except Exception as exc:
            console.print(f"[yellow]could not read the index: {exc}[/yellow]")
            return
    _print_list(root, rows[:limit], len(rows), limit, source)


def _print_list(
    root: Path, page: list[str], total: int, limit: int, source: str
) -> None:
    for p in page:
        console.print(_short_path(p, str(root)))
    shown = min(total, limit)
    tail = f" (showing {shown}, pass --limit to see more)" if total > limit else ""
    console.print(f"[dim]{total} indexed file(s) via {source}{tail}[/dim]")


def cmd_files(args: argparse.Namespace) -> None:
    root = Path(os.path.abspath(args.root))
    if args.check:
        _check(root, args.check)
    else:
        _list(root, args.pattern, args.limit)
