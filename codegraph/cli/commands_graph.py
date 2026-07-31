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

SCOPES = ["imports", "calls", "classes", "docs", "overview", "layers"]


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

    # CLI scope names differ from the MCP tool's, translate
    scope_map = {
        "imports": "file_imports",
        "calls": "call_graph",
        "classes": "class_hierarchy",
        "docs": "doc_structure",
        "overview": "full_overview",
        "layers": "layers",
    }
    args_payload = {
        "scope": scope_map.get(scope, scope),
        "symbol_name": symbol,
        "file_path": file,
        "max_nodes": max_nodes,
        "format": "mermaid",
    }
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
                # The tool returns a JSON blob {scope, format, diagram, ...}
                try:
                    inner = _json.loads(block["text"])
                    diagram = inner.get("diagram")
                    if diagram:
                        return diagram
                except Exception:
                    return block["text"]
    except Exception:
        return None
    return None


def cmd_graph(args: argparse.Namespace) -> None:
    """Generate and display a graph visualization."""
    from codegraph.core.db import get_readonly_connection
    from codegraph.viz import (
        generate_html,
        mermaid_calls,
        mermaid_classes,
        mermaid_docs,
        mermaid_imports,
        mermaid_layers,
        mermaid_overview,
        open_in_browser,
    )

    root = os.path.abspath(args.root)

    scope = args.scope
    symbol = getattr(args, "symbol", "") or ""
    file = getattr(args, "file", "") or ""
    max_nodes = args.max_nodes

    # Try the owner's HTTP endpoint first, it works even when the
    # Kuzu lock is held (which blocks readonly connections from CLI).
    mermaid_code: str | None = _fetch_mermaid_via_owner(
        root, scope, symbol, file, max_nodes
    )

    if mermaid_code is None:
        # Owner not running, open Kuzu directly.
        conn = get_readonly_connection(root)
        if conn is None:
            console.print(
                "[yellow]Graph DB is locked and no MCP owner is running.[/yellow]\n"
                "[dim]Start one with:[/dim] cgh serve  [dim]or free the lock:[/dim] "
                "pkill -f 'cgh serve'"
            )
            return

        generators = {
            "imports": lambda: mermaid_imports(conn, root, file, max_nodes),
            "calls": lambda: mermaid_calls(conn, root, symbol, max_nodes),
            "classes": lambda: mermaid_classes(conn, root, symbol, max_nodes),
            "docs": lambda: mermaid_docs(conn, root, file, max_nodes),
            "overview": lambda: mermaid_overview(conn, root, max_nodes),
            "layers": lambda: mermaid_layers(conn, root, max_nodes),
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
        default="overview",
        choices=SCOPES,
        help="What to visualize (default: overview)",
    )
    p.add_argument("--symbol", "-s", help="Filter to a symbol (for calls/classes)")
    p.add_argument("--file", "-f", help="Filter to a file (for imports/docs)")
    p.add_argument(
        "--max-nodes", "-n", type=int, default=40, help="Max nodes (default: 40)"
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
