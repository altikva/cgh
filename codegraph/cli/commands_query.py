# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands: search, lookup, callers, callees, outline.

from __future__ import annotations

import json
import os
from pathlib import Path

from rich import box
from rich.table import Table
from rich.tree import Tree

from codegraph.cli import _get_conn, _rows, _short_path, console

# ---------------------------------------------------------------------------
# cmd_search
# ---------------------------------------------------------------------------


def cmd_grep(args) -> None:
    """Regex/substring pattern search across the indexed repo."""
    import json as _json

    from codegraph.analysis.pattern import pattern_search

    root = os.path.abspath(args.root)
    hits, backend = pattern_search(
        root,
        pattern=args.pattern,
        glob=getattr(args, "glob", "") or "",
        max_results=getattr(args, "limit", 50),
        regex=not getattr(args, "fixed", False),
        case_sensitive=getattr(args, "case", False),
    )

    if args.json:
        print(
            _json.dumps(
                {
                    "pattern": args.pattern,
                    "glob": args.glob or None,
                    "backend": backend,
                    "total": len(hits),
                    "hits": [{"file": h.file, "line": h.line, "text": h.text} for h in hits],
                },
                indent=2,
            )
        )
        return

    if not hits:
        console.print(f"[dim]No matches for '[/dim]{args.pattern}[dim]'[/dim]")
        return

    console.print(f"[dim]backend: {backend} · {len(hits)} hit(s)[/dim]\n")
    for h in hits:
        short = _short_path(h.file, root)
        console.print(f"  [cyan]{short}[/cyan]:[yellow]{h.line}[/yellow]  {h.text}")


def cmd_search(args) -> None:
    root = os.path.abspath(args.root)
    query = args.query
    limit = args.limit
    offset = getattr(args, "offset", 0) or 0
    # Fetch offset+limit+1 so we can detect whether more results exist.
    fetch = offset + limit + 1
    results: list = []

    conn = _get_conn(root, readonly=True)
    if conn is None:
        # Graph DB locked (MCP server is running). Fall back to FTS — SQLite
        # supports concurrent readers, so this always works.
        try:
            from codegraph.core.fts import fts_search, get_fts_conn

            fts_conn = get_fts_conn(root)
            for hit in fts_search(fts_conn, query, limit=fetch):
                results.append((hit.kind, hit.name, hit.file_path, hit.start_line))
        except Exception as exc:
            console.print(f"[yellow]Graph DB locked and FTS unavailable: {exc}[/yellow]")
            return
    else:
        for label, kind in [("Function", "function"), ("Class", "class"), ("MdSection", "md_section")]:
            # Kuzu Cypher requires literal labels — safe: fixed allowlist
            if label == "MdSection":
                q = (
                    "MATCH (n:" + label + ") WHERE n.title CONTAINS $q "
                    "RETURN n.title AS name, n.file_path, n.start_line LIMIT $lim"
                )
            else:
                q = (
                    "MATCH (n:" + label + ") WHERE n.name CONTAINS $q "
                    "RETURN n.name, n.file_path, n.start_line LIMIT $lim"
                )
            r = conn.execute(q, {"q": query, "lim": fetch})
            for row in _rows(r):
                results.append(
                    (
                        kind,
                        row.get("name", row.get("n.name", "?")),
                        row.get("n.file_path", row.get("file_path", "?")),
                        row.get("n.start_line", row.get("start_line", "?")),
                    )
                )

    total_fetched = len(results)
    has_more = total_fetched > offset + limit
    page = results[offset : offset + limit]

    if args.json:
        out = {
            "query": query,
            "offset": offset,
            "limit": limit,
            "returned": len(page),
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "results": [{"kind": k, "name": n, "file": fp, "line": ln} for k, n, fp, ln in page],
        }
        print(json.dumps(out, indent=2))
        return

    if not page:
        if offset > 0:
            console.print(f"[dim]No more results for '[/dim][bold]{query}[/bold][dim]' at offset {offset}[/dim]")
        else:
            console.print(f"[dim]No symbols matching '[/dim][bold]{query}[/bold][dim]'[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, title=f"Search: {query}", title_style="bold")
    table.add_column("Type", width=5)
    table.add_column("Symbol", style="bold")
    table.add_column("Location", style="dim")

    icons = {"function": "[green]fn[/green]", "class": "[yellow]cls[/yellow]", "md_section": "[cyan]doc[/cyan]"}
    for kind, name, fp, line in page:
        short = _short_path(fp, root)
        table.add_row(icons.get(kind, kind), name, f"{short}:{line}")

    console.print(table)
    start = offset + 1
    end = offset + len(page)
    if has_more:
        console.print(
            f"[dim]Showing {start}–{end}. More results — next page:[/dim] "
            f"[cyan]cgh search {query} --offset {offset + limit} -n {limit}[/cyan]"
        )
    else:
        console.print(f"[dim]Showing {start}-{end} (end of results).[/dim]")


# ---------------------------------------------------------------------------
# cmd_lookup
# ---------------------------------------------------------------------------


def cmd_lookup(args) -> None:
    root = os.path.abspath(args.root)
    name = args.name
    found = False

    icons = {
        "function": "[green]fn[/green]",
        "class": "[yellow]cls[/yellow]",
        "tf_resource": "[magenta]tf[/magenta]",
        "md_section": "[cyan]doc[/cyan]",
    }

    conn = _get_conn(root, readonly=True)
    if conn is None:
        # Fallback to FTS when Kuzu graph DB is locked by MCP server
        try:
            from codegraph.core.fts import fts_search, get_fts_conn

            fts_conn = get_fts_conn(root)
            for hit in fts_search(fts_conn, name, limit=20):
                if hit.name == name or (hit.kind == "md_section" and name in hit.name):
                    found = True
                    icon = icons.get(hit.kind, hit.kind)
                    short = _short_path(hit.file_path, root)
                    console.print(
                        f"  {icon}  [bold]{hit.name}[/bold]  [dim]{short}:{hit.start_line}-{hit.end_line}[/dim]"
                    )
        except Exception as exc:
            console.print(f"[yellow]Graph DB locked and FTS unavailable: {exc}[/yellow]")
            return
    else:
        for label, kind in [
            ("Function", "function"),
            ("Class", "class"),
            ("TFResource", "tf_resource"),
            ("MdSection", "md_section"),
        ]:
            if label == "MdSection":
                q = (
                    "MATCH (n:" + label + ") WHERE n.title CONTAINS $q "
                    "RETURN n.title AS name, n.file_path, n.start_line, n.end_line"
                )
            else:
                q = "MATCH (n:" + label + ") WHERE n.name = $q RETURN n.name, n.file_path, n.start_line, n.end_line"
            r = conn.execute(q, {"q": name})
            for row in _rows(r):
                found = True
                n = row.get("name", row.get("n.name", "?"))
                fp = row.get("n.file_path", row.get("file_path", "?"))
                sl = row.get("n.start_line", row.get("start_line", "?"))
                el = row.get("n.end_line", row.get("end_line", "?"))
                icon = icons.get(kind, kind)
                short = _short_path(fp, root)
                console.print(f"  {icon}  [bold]{n}[/bold]  [dim]{short}:{sl}-{el}[/dim]")

    if not found:
        console.print(f"[dim]No symbol found matching '[/dim][bold]{name}[/bold][dim]'[/dim]")


# ---------------------------------------------------------------------------
# cmd_callers
# ---------------------------------------------------------------------------


def cmd_callers(args) -> None:
    root = os.path.abspath(args.root)
    conn = _get_conn(root, readonly=True)
    if conn is None:
        console.print("[yellow]Graph DB is locked (indexing?). Try again later.[/yellow]")
        return
    r = conn.execute(
        "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
        "WHERE callee.name = $n "
        "RETURN caller.name, caller.file_path, caller.start_line",
        {"n": args.fn_name},
    )
    rows = _rows(r)
    if not rows:
        console.print(f"[dim]No callers of '[/dim][bold]{args.fn_name}[/bold][dim]' found[/dim]")
        return

    tree = Tree(f"[bold yellow]{args.fn_name}[/bold yellow] [dim]is called by:[/dim]")
    for row in rows:
        short = _short_path(row["caller.file_path"], root)
        tree.add(f"[green]{row['caller.name']}[/green]  [dim]{short}:{row['caller.start_line']}[/dim]")
    console.print(tree)


# ---------------------------------------------------------------------------
# cmd_callees
# ---------------------------------------------------------------------------


def cmd_callees(args) -> None:
    root = os.path.abspath(args.root)
    conn = _get_conn(root, readonly=True)
    if conn is None:
        console.print("[yellow]Graph DB is locked (indexing?). Try again later.[/yellow]")
        return
    r = conn.execute(
        "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
        "WHERE caller.name = $n "
        "RETURN callee.name, callee.file_path, callee.start_line",
        {"n": args.fn_name},
    )
    rows = _rows(r)
    if not rows:
        console.print(f"[dim]'{args.fn_name}' calls no indexed functions[/dim]")
        return

    tree = Tree(f"[bold green]{args.fn_name}[/bold green] [dim]calls:[/dim]")
    for row in rows:
        short = _short_path(row["callee.file_path"], root)
        tree.add(f"[yellow]{row['callee.name']}[/yellow]  [dim]{short}:{row['callee.start_line']}[/dim]")
    console.print(tree)


# ---------------------------------------------------------------------------
# cmd_outline
# ---------------------------------------------------------------------------


def cmd_outline(args) -> None:
    root = os.path.abspath(args.root)
    conn = _get_conn(root, readonly=True)
    if conn is None:
        console.print("[yellow]Graph DB is locked (indexing?). Try again later.[/yellow]")
        return
    file_path = args.file
    if not os.path.isabs(file_path):
        file_path = str(Path(root) / file_path)

    r = conn.execute(
        "MATCH (s:MdSection) WHERE s.file_path = $p "
        "OR s.file_path ENDS WITH $suffix "
        "OR lower(s.file_path) = lower($p) "
        "OR lower(s.file_path) ENDS WITH lower($suffix) "
        "RETURN s.title, s.level, s.start_line, s.end_line "
        "ORDER BY s.start_line",
        {"p": file_path, "suffix": args.file},
    )
    rows = _rows(r)
    if not rows:
        console.print(f"[dim]No sections found in '{args.file}' (is it indexed?)[/dim]")
        return

    # Build tree
    tree = Tree(f"[bold cyan]{args.file}[/bold cyan]")
    seen = set()
    node_stack: list[tuple[int, Tree]] = [(0, tree)]  # (level, tree_node)

    for row in rows:
        key = (row["s.start_line"], row["s.title"])
        if key in seen:
            continue
        seen.add(key)

        level = row["s.level"]
        title = row["s.title"]
        line = row["s.start_line"]

        # Pop stack to find parent
        while len(node_stack) > 1 and node_stack[-1][0] >= level:
            node_stack.pop()

        parent = node_stack[-1][1]
        level_colors = {1: "bold cyan", 2: "green", 3: "yellow", 4: "dim", 5: "dim", 6: "dim"}
        style = level_colors.get(level, "dim")

        child = parent.add(f"[{style}]{title}[/{style}] [dim]L{line}[/dim]")
        node_stack.append((level, child))

    console.print(tree)
