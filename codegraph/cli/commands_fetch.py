# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh fetch <url>` indexes a page into the searchable
#              store; `cgh fetch --search Q` reads it back; `--purge`
#              clears it. A fetch is gated network egress (see
#              analysis/fetch_index): http/https, no private hosts, off
#              in secure mode unless allow_fetch, always audited.

from __future__ import annotations

import argparse
import os

from codegraph.cli import console


def cmd_fetch(args: argparse.Namespace) -> None:
    from codegraph.analysis.fetch_index import (
        FetchError,
        fetch_and_index,
        purge_fetched,
        search_fetched,
    )
    from codegraph.core.config import load_config

    root = os.path.abspath(args.root)

    if args.purge is not None:
        n = purge_fetched(root, url=args.purge or "")
        console.print(f"[green]+[/green] purged {n} chunk(s)")
        return
    if args.search:
        hits = search_fetched(root, args.search, limit=args.limit)
        if not hits:
            console.print("[dim]no fetched content matches.[/dim]")
            return
        for h in hits:
            console.print(
                f"[cyan]{h['title'] or h['url']}[/cyan] [dim]{h['url']}[/dim]"
            )
            console.print(f"  {h['snippet']}\n")
        return
    if not args.url:
        console.print("[red]usage: cgh fetch <url> | --search Q | --purge [url][/red]")
        raise SystemExit(2)

    cfg = {"allow_fetch": load_config(root).allow_fetch}
    try:
        out = fetch_and_index(
            root, args.url, ttl_hours=args.ttl, force=args.force, config=cfg
        )
    except FetchError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    state = "cached" if out["cached"] else "indexed"
    console.print(
        f"[green]+[/green] {state} [cyan]{out['url']}[/cyan] "
        f"({out['chunks']} chunk(s)); search with [cyan]cgh fetch --search ...[/cyan]"
    )


def register_fetch_parser(sub) -> None:
    from codegraph.__main__ import _add_root

    p = sub.add_parser("fetch", help="Fetch a URL into the searchable index")
    _add_root(p)
    p.add_argument("url", nargs="?", help="URL to fetch and index")
    p.add_argument("--search", default="", help="Search already-fetched content")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--ttl", type=float, default=24.0, help="Cache hours (default 24)")
    p.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    p.add_argument(
        "--purge",
        nargs="?",
        const="",
        default=None,
        help="Drop fetched content (a URL, or all if none given)",
    )
    p.set_defaults(func=cmd_fetch)
