# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands: graph visualization and add-dir.

from __future__ import annotations

import argparse
import os
from pathlib import Path

from rich.panel import Panel

from codegraph.cli import console

# "explore" is the interactive whole-graph view; the others render a
# Mermaid diagram, which stays readable only at a few dozen nodes.
SCOPES = ["explore", "imports", "calls", "classes", "docs", "overview", "layers"]


# ---------------------------------------------------------------------------
# cmd_graph
# ---------------------------------------------------------------------------


def _fetch_mermaid_via_owner(
    root: str, scope: str, symbol: str, file: str, max_nodes: int
) -> str | None:
    """
    Ask the running MCP owner to build the Mermaid diagram for us.
    Works while the owner holds the Kuzu write lock (which blocks our
    own readonly connection). Returns None if the owner isn't running
    or the call fails.
    """
    # CLI scope names differ from the MCP tool's, translate
    scope_map = {
        "imports": "file_imports",
        "calls": "call_graph",
        "classes": "class_hierarchy",
        "docs": "doc_structure",
        "overview": "full_overview",
        "layers": "layers",
    }
    result = _call_owner_visualize(
        root,
        {
            "scope": scope_map.get(scope, scope),
            "symbol_name": symbol,
            "file_path": file,
            "max_nodes": max_nodes,
            "format": "mermaid",
        },
    )
    if isinstance(result, dict):
        return result.get("diagram") or None
    return result


def _call_owner_visualize(root: str, args_payload: dict):
    """POST one visualize_graph call to the owner, return its parsed JSON
    result (a dict), the raw text when it is not JSON, or None."""
    import http.client
    import json as _json

    from codegraph.state.auth import ensure_auth_key
    from codegraph.state.ipc import is_owner_alive, read_owner_port

    if not is_owner_alive(root):
        return None
    port = read_owner_port(root)
    if not port:
        return None
    token = ensure_auth_key(root)
    body = _json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "visualize_graph", "arguments": args_payload},
        }
    )
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        c.request(
            "POST",
            "/mcp",
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
        )
        resp = c.getresponse()
        if resp.status != 200:
            return None
        payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
        result = payload.get("result") or {}
        content = result.get("content") or []
        for block in content:
            if block.get("type") == "text" and block.get("text"):
                # The tool returns a JSON blob {scope, format, diagram|payload}
                try:
                    return _json.loads(block["text"])
                except Exception:
                    return block["text"]
    except Exception:
        return None
    return None


def _fetch_payload_via_owner(root: str, max_symbols: int) -> dict | None:
    """Ask a running owner for the whole-graph payload.

    Same reason as _fetch_mermaid_via_owner: the owner holds the write lock,
    which blocks our own read-only open.
    """
    result = _call_owner_visualize(
        root, {"scope": "explore", "max_nodes": max_symbols, "format": "json"}
    )
    if not isinstance(result, dict):
        return None
    payload = result.get("payload")
    return payload if isinstance(payload, dict) else None


def cmd_graph(args: argparse.Namespace) -> None:
    """Generate and display a graph visualization."""
    from codegraph.core.db import get_readonly_connection
    from codegraph.viz import generate_html, open_in_browser
    from codegraph.viz.graphviews import (
        viz_call_graph,
        viz_class_hierarchy,
        viz_doc_structure,
        viz_file_imports,
        viz_full_overview,
        viz_layers,
    )

    root = os.path.abspath(args.root)

    scope = args.scope
    symbol = getattr(args, "symbol", "") or ""
    file = getattr(args, "file", "") or ""
    max_nodes = args.max_nodes

    if scope == "explore":
        _graph_explore(args, root)
        return

    # Try the owner's HTTP endpoint first, it works even when the
    # graph DB write lock is held (which blocks readonly CLI opens).
    mermaid_code: str | None = _fetch_mermaid_via_owner(
        root, scope, symbol, file, max_nodes
    )

    if mermaid_code is None:
        # Owner not running, open the graph DB directly.
        conn = get_readonly_connection(root)
        if conn is None:
            console.print(
                "[yellow]Graph DB is locked and no MCP owner is running.[/yellow]\n"
                "[dim]Start one with:[/dim] cgh serve  [dim]or free the lock:[/dim] "
                "pkill -f 'cgh serve'"
            )
            return

        generators = {
            "imports": lambda: viz_file_imports(conn, root, file, max_nodes, "mermaid"),
            "calls": lambda: viz_call_graph(conn, root, symbol, max_nodes, "mermaid"),
            "classes": lambda: viz_class_hierarchy(
                conn, root, symbol, max_nodes, "mermaid"
            ),
            "docs": lambda: viz_doc_structure(conn, root, file, max_nodes, "mermaid"),
            "overview": lambda: viz_full_overview(conn, root, max_nodes, "mermaid"),
            "layers": lambda: viz_layers(conn, root, "mermaid"),
        }
        mermaid_code = generators[scope]()

    # Output based on format flags
    if args.mermaid:
        # Raw mermaid to stdout
        console.print(mermaid_code)
        return

    if args.html:
        # Write HTML to file
        out_path = Path(args.html)
        meta = f"scope={scope}"
        if symbol:
            meta += f" symbol={symbol}"
        if file:
            meta += f" file={file}"
        html_content = generate_html(mermaid_code, scope, root, meta)
        out_path.write_text(html_content, encoding="utf-8")
        console.print(
            f"  [green]+[/green] {out_path} [dim]({len(html_content):,} bytes)[/dim]"
        )
        return

    # Default: generate HTML and open in browser
    meta = f"scope={scope}"
    if symbol:
        meta += f" symbol={symbol}"
    if file:
        meta += f" file={file}"
    html_content = generate_html(mermaid_code, scope, root, meta)
    out = open_in_browser(html_content, f"codegraph-{scope}.html")
    console.print(
        Panel(
            f"  [green]Opened in browser[/green]\n"
            f"  [dim]File:[/dim] {out}\n"
            f"  [dim]Scope:[/dim] {scope}\n"
            f"  [dim]Nodes:[/dim] {max_nodes} max",
            title="[bold cyan]codegraph[/bold cyan]",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# cmd_add_dir
# ---------------------------------------------------------------------------


def _graph_explore(args: argparse.Namespace, root: str) -> None:
    """The interactive view: whole graph, canvas force layout, in a browser."""
    from codegraph.core.db import get_readonly_connection
    from codegraph.viz import generate_graph_view_html, open_in_browser
    from codegraph.viz.graphdata import build_graph_payload

    max_symbols = getattr(args, "max_symbols", 400)
    if args.mermaid:
        console.print(
            "[yellow]--mermaid needs a diagram scope.[/yellow]\n"
            "[dim]Try:[/dim] cgh graph overview --mermaid"
        )
        return

    payload = _fetch_payload_via_owner(root, max_symbols)
    if payload is None:
        conn = get_readonly_connection(root)
        if conn is None:
            console.print(
                "[yellow]Graph DB is locked and no MCP owner is running.[/yellow]\n"
                "[dim]Start one with:[/dim] cgh serve  [dim]or free the lock:[/dim] "
                "pkill -f 'cgh serve'"
            )
            return
        payload = build_graph_payload(conn, root, max_symbols)

    totals = payload.get("totals", {})
    html_content = generate_graph_view_html(payload, root)

    if args.html:
        out_path = Path(args.html)
        out_path.write_text(html_content, encoding="utf-8")
        console.print(
            f"  [green]+[/green] {out_path} [dim]({len(html_content):,} bytes)[/dim]"
        )
        return

    out = open_in_browser(html_content, "codegraph-graph.html")
    console.print(
        Panel(
            f"  [green]Opened in browser[/green]\n"
            f"  [dim]File:[/dim] {out}\n"
            f"  [dim]Files:[/dim] {len(payload.get('files', [])):,} "
            f"[dim]with[/dim] {len(payload.get('imports', [])):,} [dim]imports[/dim]\n"
            f"  [dim]Symbols:[/dim] {totals.get('symbols_shown', 0):,} "
            f"[dim]most-called of[/dim] {totals.get('functions', 0):,}",
            title="[bold cyan]codegraph[/bold cyan]",
            border_style="cyan",
        )
    )


def cmd_add_dir(args: argparse.Namespace) -> None:
    """Add or manage extra directories in the graph."""
    from codegraph.core.config import CODEGRAPH_DIR, CONFIG_FILE

    root = Path(os.path.abspath(args.root))
    config_path = root / CODEGRAPH_DIR / CONFIG_FILE

    if not config_path.exists():
        console.print("[yellow]Not initialized. Run 'cgh init' first.[/yellow]")
        return

    # Read current config
    import tomllib

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    extra_dirs = data.get("codegraph", {}).get("extra_dirs", [])

    # List mode
    if args.action == "list" or (args.action is None and not args.paths):
        if not extra_dirs:
            console.print("[dim]No extra directories configured.[/dim]")
            console.print("[dim]Add with: cgh add-dir add ../frontend[/dim]")
        else:
            console.print("[bold]Extra directories:[/bold]\n")
            for d in extra_dirs:
                resolved = (root / d).resolve()
                exists = resolved.exists()
                status = "[green]OK[/green]" if exists else "[red]missing[/red]"
                console.print(f"  {status}  {d}  [dim]({resolved})[/dim]")
        return

    # Add mode
    if args.action == "add" and args.paths:
        added = []
        for p in args.paths:
            resolved = Path(p).resolve()
            rel = os.path.relpath(resolved, root)
            if rel in extra_dirs:
                console.print(f"  [dim]Already added:[/dim] {rel}")
                continue
            if not resolved.exists():
                console.print(f"  [yellow]Warning:[/yellow] {rel} does not exist yet")
            extra_dirs.append(rel)
            added.append(rel)

        if added:
            _write_extra_dirs(config_path, data, extra_dirs)
            for d in added:
                console.print(f"  [green]+[/green] {d}")
            console.print("\n[dim]Run 'cgh index' to include these directories.[/dim]")
        return

    # Remove mode
    if args.action == "remove" and args.paths:
        removed = []
        for p in args.paths:
            resolved = Path(p).resolve()
            rel = os.path.relpath(resolved, root)
            if rel in extra_dirs:
                extra_dirs.remove(rel)
                removed.append(rel)
            else:
                console.print(f"  [dim]Not found:[/dim] {rel}")

        if removed:
            _write_extra_dirs(config_path, data, extra_dirs)
            for d in removed:
                console.print(f"  [red]-[/red] {d}")
        return

    console.print(
        "[dim]Usage: cgh add-dir add <path> | cgh add-dir remove <path> | cgh add-dir list[/dim]"
    )


def _write_extra_dirs(config_path: Path, data: dict, extra_dirs: list[str]) -> None:
    """Update extra_dirs in config.toml (preserves other settings)."""
    content = config_path.read_text(encoding="utf-8")

    # Check if extra_dirs already exists in file
    if "extra_dirs" in content:
        import re

        # Replace existing extra_dirs line
        dirs_str = ", ".join(f'"{d}"' for d in extra_dirs)
        content = re.sub(
            r"extra_dirs\s*=\s*\[.*?\]",
            f"extra_dirs = [{dirs_str}]",
            content,
            flags=re.DOTALL,
        )
    else:
        # Add after [codegraph] section
        insert_after = "[codegraph]"
        if insert_after in content:
            dirs_str = ", ".join(f'"{d}"' for d in extra_dirs)
            content = content.replace(
                insert_after,
                f"{insert_after}\n# Additional directories to include in the graph\nextra_dirs = [{dirs_str}]",
            )

    config_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# register_graph_parser (argparse setup)
# ---------------------------------------------------------------------------


def register_graph_parser(sub) -> None:
    """Register graph and add-dir subcommands with argparse."""

    # --- graph ---
    p = sub.add_parser("graph", help="Visualize the code graph (opens in browser)")
    p.add_argument(
        "scope",
        nargs="?",
        default="explore",
        choices=SCOPES,
        help="What to visualize (default: explore, the interactive whole graph)",
    )
    p.add_argument("--symbol", "-s", help="Filter to a symbol (for calls/classes)")
    p.add_argument("--file", "-f", help="Filter to a file (for imports/docs)")
    p.add_argument(
        "--max-nodes", "-n", type=int, default=40, help="Max nodes (default: 40)"
    )
    p.add_argument(
        "--max-symbols",
        type=int,
        default=400,
        help="Functions carried into the explore view, by call degree (default: 400)",
    )
    p.add_argument(
        "--mermaid", action="store_true", help="Output raw Mermaid to stdout"
    )
    p.add_argument(
        "--html", metavar="FILE", help="Write HTML to file instead of opening browser"
    )
    p.add_argument("--root", default=os.getcwd())

    # --- add-dir ---
    p = sub.add_parser("add-dir", help="Manage extra directories in the graph")
    p.add_argument(
        "action",
        nargs="?",
        choices=["add", "remove", "list"],
        help="Action (default: list)",
    )
    p.add_argument("paths", nargs="*", help="Directory paths")
    p.add_argument("--root", default=os.getcwd())
