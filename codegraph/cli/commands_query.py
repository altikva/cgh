# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands: search, lookup, callers, callees, outline.
#              Queries go through the backend-neutral GraphDB protocol
#              (find_nodes / find_neighbors), so they work on DuckDB and
#              Kuzu alike, and federate across subrepos with a scope tag.

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rich import box
from rich.table import Table
from rich.tree import Tree

from codegraph.analysis.federation import (
    child_fts_symbol_lookup,
    child_fts_symbol_search,
    for_each_child_graphdb,
    has_subrepos,
)
from codegraph.cli import _get_conn, _short_path, console

# ---------------------------------------------------------------------------
# cmd_grep
# ---------------------------------------------------------------------------


def cmd_grep(args: argparse.Namespace) -> None:
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
                    "hits": [
                        {"file": h.file, "line": h.line, "text": h.text} for h in hits
                    ],
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


# ---------------------------------------------------------------------------
# Federation helper: run a per-connection query on each subrepo, tag results
# with the child's scope name. Children whose DB is missing or locked are
# reported as warnings, never as a crash.
# ---------------------------------------------------------------------------


def _query_children_scoped(
    root: str, fn
) -> tuple[list[tuple[str, list]], list[tuple[str, str]]]:
    """Run ``fn(conn)`` on each subrepo's RO graph DB, one bucket per scope.

    Returns ``([(scope, rows), …], failures)`` where failures are
    ``(scope, error)`` pairs. Keeping the rows grouped lets callers merge
    scopes fairly instead of concatenating them, which matters as soon as
    the output is paginated; keeping the failures scoped lets them retry a
    single child through another backend.
    """
    buckets: list[tuple[str, list]] = []
    failures: list[tuple[str, str]] = []
    if not has_subrepos(root):
        return buckets, failures
    for scoped in for_each_child_graphdb(root, lambda conn, _r: fn(conn)):
        if scoped.error:
            failures.append((scoped.scope, scoped.error))
            continue
        buckets.append((scoped.scope, list(scoped.payload or [])))
    return buckets, failures


def _fmt_scope_errors(failures: list[tuple[str, str]]) -> list[str]:
    return [f"{scope}: {error}" for scope, error in failures]


def _query_children(root: str, fn) -> tuple[list[tuple], list[str]]:
    """Flat variant of :func:`_query_children_scoped`.

    Returns ``(rows, warnings)`` where each row is ``(scope, *fn_row)``
    and warnings are per-scope error strings.
    """
    buckets, failures = _query_children_scoped(root, fn)
    rows = [(scope, *item) for scope, items in buckets for item in items]
    return rows, _fmt_scope_errors(failures)


def _interleave(buckets: list[tuple[str, list]]) -> list[tuple]:
    """Round-robin merge of per-scope rows, tagged with their scope.

    A plain parent-then-children concatenation makes the page slice swallow
    every child result whenever the parent alone fills the page. Taking one
    row per scope per round gives every scope a share of the first page.
    """
    merged: list[tuple] = []
    idx = 0
    while True:
        added = False
        for scope, rows in buckets:
            if idx < len(rows):
                merged.append((scope, *rows[idx]))
                added = True
        if not added:
            return merged
        idx += 1


def _print_scope_warnings(warnings: list[str]) -> None:
    for w in warnings:
        console.print(f"[yellow]⚠ subrepo {w}[/yellow]")


# ---------------------------------------------------------------------------
# cmd_search
# ---------------------------------------------------------------------------


def _search_symbols_conn(conn, query: str, fetch: int) -> list[tuple]:
    """(kind, name, file_path, start_line) rows for one graph DB."""
    out: list[tuple] = []
    for label, kind in [("Function", "function"), ("Class", "class")]:
        for row in conn.find_nodes(
            label,
            contains={"name": query},
            return_fields=["name", "file_path", "start_line"],
            limit=fetch,
        ):
            out.append((kind, row["name"], row["file_path"], row["start_line"]))
    for row in conn.find_nodes(
        "MdSection",
        contains={"title": query},
        return_fields=["title", "file_path", "start_line"],
        limit=fetch,
    ):
        out.append(("md_section", row["title"], row["file_path"], row["start_line"]))
    return out


def _fts_rows(hits) -> list[tuple]:
    """(kind, name, file_path, start_line) rows from FTS hits."""
    return [(h.kind, h.name, h.file_path, h.start_line) for h in hits]


def _child_fts_fallback(
    root: str, query: str, fetch: int, scopes: set[str]
) -> tuple[list[tuple[str, list]], list[tuple[str, str]]]:
    """Search the named children through their FTS db instead of the graph."""
    buckets, failures = child_fts_symbol_search(root, query, fetch, scopes)
    return [(scope, _fts_rows(hits)) for scope, hits in buckets], failures


def cmd_search(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)
    query = args.query
    limit = args.limit
    offset = getattr(args, "offset", 0) or 0
    # Fetch offset+limit+1 so we can detect whether more results exist.
    fetch = offset + limit + 1
    # Buckets are (scope, [(kind, name, file_path, start_line), …]).
    buckets: list[tuple[str, list]] = []

    conn = _get_conn(root, readonly=True)
    if conn is None:
        # Graph DB locked (MCP server is running). Fall back to FTS, SQLite
        # supports concurrent readers, so this always works.
        try:
            from codegraph.core.fts import fts_search, get_fts_conn

            fts_conn = get_fts_conn(root)
            buckets.append(
                ("parent", _fts_rows(fts_search(fts_conn, query, limit=fetch)))
            )
        except Exception as exc:
            console.print(
                f"[yellow]Graph DB locked and FTS unavailable: {exc}[/yellow]"
            )
            return
    else:
        buckets.append(("parent", _search_symbols_conn(conn, query, fetch)))

    child_buckets, child_failures = _query_children_scoped(
        root, lambda c: _search_symbols_conn(c, query, fetch)
    )
    buckets.extend(child_buckets)
    if child_failures:
        fts_buckets, child_failures = _child_fts_fallback(
            root, query, fetch, {scope for scope, _ in child_failures}
        )
        buckets.extend(fts_buckets)
    warnings = _fmt_scope_errors(child_failures)
    federated = has_subrepos(root)

    # Round-robin across scopes: parent-first concatenation would push every
    # child result past the page slice on repos where the parent alone fills it.
    results = _interleave(buckets)
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
            "results": [
                {"scope": s, "kind": k, "name": n, "file": fp, "line": ln}
                for s, k, n, fp, ln in page
            ],
        }
        if warnings:
            out["warnings"] = warnings
        print(json.dumps(out, indent=2))
        return

    _print_scope_warnings(warnings)
    if not page:
        if offset > 0:
            console.print(
                f"[dim]No more results for '[/dim][bold]{query}[/bold][dim]' at offset {offset}[/dim]"
            )
        else:
            console.print(
                f"[dim]No symbols matching '[/dim][bold]{query}[/bold][dim]'[/dim]"
            )
        return

    table = Table(box=box.SIMPLE_HEAD, title=f"Search: {query}", title_style="bold")
    table.add_column("Type", width=5)
    table.add_column("Symbol", style="bold")
    table.add_column("Location", style="dim")
    if federated:
        table.add_column("Scope", style="dim")

    icons = {
        "function": "[green]fn[/green]",
        "class": "[yellow]cls[/yellow]",
        "md_section": "[cyan]doc[/cyan]",
    }
    for scope, kind, name, fp, line in page:
        short = _short_path(fp, root)
        cells = [icons.get(kind, kind), name, f"{short}:{line}"]
        if federated:
            cells.append(scope)
        table.add_row(*cells)

    console.print(table)
    start = offset + 1
    end = offset + len(page)
    if has_more:
        console.print(
            f"[dim]Showing {start}-{end}. More results, next page:[/dim] "
            f"[cyan]cgh search {query} --offset {offset + limit} -n {limit}[/cyan]"
        )
    else:
        console.print(f"[dim]Showing {start}-{end} (end of results).[/dim]")


# ---------------------------------------------------------------------------
# cmd_lookup
# ---------------------------------------------------------------------------


def _lookup_conn(conn, name: str) -> list[tuple]:
    """(kind, name, file_path, start_line, end_line) rows for one graph DB."""
    out: list[tuple] = []
    for label, kind in [
        ("Function", "function"),
        ("Class", "class"),
        ("TFResource", "tf_resource"),
    ]:
        for row in conn.find_nodes(
            label,
            where={"name": name},
            return_fields=["name", "file_path", "start_line", "end_line"],
        ):
            out.append(
                (
                    kind,
                    row["name"],
                    row["file_path"],
                    row["start_line"],
                    row["end_line"],
                )
            )
    # TFVar has no end_line column (a variable/output block is anchored by its
    # start), so it needs its own loop; reuse start_line for the end slot.
    for row in conn.find_nodes(
        "TFVar",
        where={"name": name},
        return_fields=["name", "file_path", "start_line"],
    ):
        out.append(
            (
                "tf_var",
                row["name"],
                row["file_path"],
                row["start_line"],
                row["start_line"],
            )
        )
    for row in conn.find_nodes(
        "MdSection",
        contains={"title": name},
        return_fields=["title", "file_path", "start_line", "end_line"],
    ):
        out.append(
            (
                "md_section",
                row["title"],
                row["file_path"],
                row["start_line"],
                row["end_line"],
            )
        )
    return out


def cmd_lookup(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)
    name = args.name
    found = False

    icons = {
        "function": "[green]fn[/green]",
        "class": "[yellow]cls[/yellow]",
        "tf_resource": "[magenta]tf[/magenta]",
        "tf_var": "[magenta]var[/magenta]",
        "md_section": "[cyan]doc[/cyan]",
    }
    federated = has_subrepos(root)

    def _print_hit(scope: str, kind: str, n: str, fp: str, sl, el) -> None:
        icon = icons.get(kind, kind)
        short = _short_path(fp, root)
        scope_tag = f"  [dim]({scope})[/dim]" if federated and scope != "parent" else ""
        console.print(
            f"  {icon}  [bold]{n}[/bold]  [dim]{short}:{sl}-{el}[/dim]{scope_tag}"
        )

    conn = _get_conn(root, readonly=True)
    if conn is None:
        # Fallback to FTS when the graph DB is locked by the MCP server
        try:
            from codegraph.core.fts import fts_search, get_fts_conn

            fts_conn = get_fts_conn(root)
            for hit in fts_search(fts_conn, name, limit=20):
                if hit.name == name or (hit.kind == "md_section" and name in hit.name):
                    found = True
                    _print_hit(
                        "parent",
                        hit.kind,
                        hit.name,
                        hit.file_path,
                        hit.start_line,
                        hit.end_line,
                    )
        except Exception as exc:
            console.print(
                f"[yellow]Graph DB locked and FTS unavailable: {exc}[/yellow]"
            )
            return
    else:
        for kind, n, fp, sl, el in _lookup_conn(conn, name):
            found = True
            _print_hit("parent", kind, n, fp, sl, el)

    child_buckets, child_failures = _query_children_scoped(
        root, lambda c: _lookup_conn(c, name)
    )
    if child_failures:
        # A child whose own owner holds the graph write lock still resolves
        # the name through its FTS index.
        fts_buckets, child_failures = child_fts_symbol_lookup(
            root, name, {scope for scope, _ in child_failures}
        )
        child_buckets.extend(
            (
                scope,
                [(h.kind, h.name, h.file_path, h.start_line, h.end_line) for h in hits],
            )
            for scope, hits in fts_buckets
        )
    for scope, rows in child_buckets:
        for kind, n, fp, sl, el in rows:
            found = True
            _print_hit(scope, kind, n, fp, sl, el)
    _print_scope_warnings(_fmt_scope_errors(child_failures))

    if not found:
        console.print(
            f"[dim]No symbol found matching '[/dim][bold]{name}[/bold][dim]'[/dim]"
        )


# ---------------------------------------------------------------------------
# cmd_callers
# ---------------------------------------------------------------------------


def _callers_conn(conn, fn_name: str) -> list[tuple]:
    return [
        (row["src_name"], row["src_file_path"], row["src_start_line"])
        for row in conn.find_neighbors(
            "CALLS",
            dst_where={"name": fn_name},
            return_src=["name", "file_path", "start_line"],
        )
    ]


def cmd_callers(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)
    federated = has_subrepos(root)
    rows: list[tuple] = []

    conn = _get_conn(root, readonly=True)
    if conn is None:
        console.print(
            "[yellow]Graph DB is locked (indexing?). Parent scope skipped.[/yellow]"
            if federated
            else "[yellow]Graph DB is locked (indexing?). Try again later.[/yellow]"
        )
    else:
        rows = [("parent", *r) for r in _callers_conn(conn, args.fn_name)]

    child_rows, warnings = _query_children(
        root, lambda c: _callers_conn(c, args.fn_name)
    )
    rows.extend(child_rows)
    _print_scope_warnings(warnings)

    if not rows:
        console.print(
            f"[dim]No callers of '[/dim][bold]{args.fn_name}[/bold][dim]' found[/dim]"
        )
        return

    tree = Tree(f"[bold yellow]{args.fn_name}[/bold yellow] [dim]is called by:[/dim]")
    for scope, name, fp, line in rows:
        short = _short_path(fp, root)
        scope_tag = f"  [dim]({scope})[/dim]" if federated and scope != "parent" else ""
        tree.add(f"[green]{name}[/green]  [dim]{short}:{line}[/dim]{scope_tag}")
    console.print(tree)


# ---------------------------------------------------------------------------
# cmd_callees
# ---------------------------------------------------------------------------


def _callees_conn(conn, fn_name: str) -> list[tuple]:
    return [
        (row["dst_name"], row["dst_file_path"], row["dst_start_line"])
        for row in conn.find_neighbors(
            "CALLS",
            src_where={"name": fn_name},
            return_dst=["name", "file_path", "start_line"],
        )
    ]


def cmd_callees(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)
    federated = has_subrepos(root)
    rows: list[tuple] = []

    conn = _get_conn(root, readonly=True)
    if conn is None:
        console.print(
            "[yellow]Graph DB is locked (indexing?). Parent scope skipped.[/yellow]"
            if federated
            else "[yellow]Graph DB is locked (indexing?). Try again later.[/yellow]"
        )
    else:
        rows = [("parent", *r) for r in _callees_conn(conn, args.fn_name)]

    child_rows, warnings = _query_children(
        root, lambda c: _callees_conn(c, args.fn_name)
    )
    rows.extend(child_rows)
    _print_scope_warnings(warnings)

    if not rows:
        console.print(f"[dim]'{args.fn_name}' calls no indexed functions[/dim]")
        return

    tree = Tree(f"[bold green]{args.fn_name}[/bold green] [dim]calls:[/dim]")
    for scope, name, fp, line in rows:
        short = _short_path(fp, root)
        scope_tag = f"  [dim]({scope})[/dim]" if federated and scope != "parent" else ""
        tree.add(f"[yellow]{name}[/yellow]  [dim]{short}:{line}[/dim]{scope_tag}")
    console.print(tree)


# ---------------------------------------------------------------------------
# cmd_outline
# ---------------------------------------------------------------------------


def _outline_conn(conn, abs_path: str, rel_arg: str) -> list[dict]:
    """MdSection rows for one file, exact path first, then suffix match."""
    rows = conn.find_nodes(
        "MdSection",
        where={"file_path": abs_path},
        return_fields=["title", "level", "start_line", "end_line"],
        order_by=["start_line"],
    )
    if rows:
        return rows
    # Suffix fallback: the indexed path may differ in prefix (extra_dirs,
    # symlinks). Substring-match on the argument, then keep true suffixes.
    candidates = conn.find_nodes(
        "MdSection",
        contains={"file_path": rel_arg},
        return_fields=["file_path", "title", "level", "start_line", "end_line"],
        order_by=["start_line"],
    )
    norm = rel_arg.lower().replace("\\", "/")
    return [
        r
        for r in candidates
        if (r.get("file_path") or "").lower().replace("\\", "/").endswith(norm)
    ]


def cmd_outline(args: argparse.Namespace) -> None:
    root = os.path.abspath(args.root)
    conn = _get_conn(root, readonly=True)

    file_path = args.file
    if not os.path.isabs(file_path):
        file_path = str(Path(root) / file_path)

    rows: list[dict] = []
    scope = "parent"
    if conn is None:
        console.print(
            "[yellow]Graph DB is locked (indexing?). Parent scope skipped.[/yellow]"
        )
    else:
        rows = _outline_conn(conn, file_path, args.file)

    if not rows and has_subrepos(root):
        # The file may belong to a federated subrepo, first scope wins.
        def _child_outline(c, child_root):
            child_abs = args.file
            if not os.path.isabs(child_abs):
                child_abs = str(Path(child_root) / child_abs)
            return _outline_conn(c, child_abs, args.file)

        for scoped in for_each_child_graphdb(root, _child_outline):
            if scoped.error or not scoped.payload:
                continue
            rows = scoped.payload
            scope = scoped.scope
            break

    if not rows:
        console.print(f"[dim]No sections found in '{args.file}' (is it indexed?)[/dim]")
        return

    # Build tree
    title = f"[bold cyan]{args.file}[/bold cyan]"
    if scope != "parent":
        title += f"  [dim]({scope})[/dim]"
    tree = Tree(title)
    seen = set()
    node_stack: list[tuple[int, Tree]] = [(0, tree)]  # (level, tree_node)

    for row in rows:
        key = (row["start_line"], row["title"])
        if key in seen:
            continue
        seen.add(key)

        level = row["level"]
        section_title = row["title"]
        line = row["start_line"]

        # Pop stack to find parent
        while len(node_stack) > 1 and node_stack[-1][0] >= level:
            node_stack.pop()

        parent = node_stack[-1][1]
        level_colors = {
            1: "bold cyan",
            2: "green",
            3: "yellow",
            4: "dim",
            5: "dim",
            6: "dim",
        }
        style = level_colors.get(level, "dim")

        child = parent.add(f"[{style}]{section_title}[/{style}] [dim]L{line}[/dim]")
        node_stack.append((level, child))

    console.print(tree)
