# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP visualization tools: visualize_graph, graph_stats.
#              Thin MCP facade over codegraph.viz.graphviews (one
#              implementation shared with the `cgh graph` CLI).

from __future__ import annotations

import json

from codegraph.viz.graphviews import (
    viz_call_graph,
    viz_class_hierarchy,
    viz_doc_structure,
    viz_file_imports,
    viz_file_symbols,
    viz_full_overview,
    viz_layers,
)


def register(mcp) -> None:
    """Register visualization tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _logged_tool

    # -------------------------------------------------------------------
    # Internal diagram generators (use _short_path which depends on _root)
    # -------------------------------------------------------------------

    @mcp.tool()
    @_logged_tool
    def visualize_graph(
        scope: str = "file_imports",
        file_path: str = "",
        symbol_name: str = "",
        max_nodes: int = 30,
        format: str = "mermaid",
    ) -> str:
        """
        Generate a visual graph diagram of code relationships.
        Returns Mermaid markdown that can be rendered in any Mermaid viewer.

        Args:
            scope: what to visualize:
                - "file_imports": file-level import graph (default)
                - "call_graph": function call relationships
                - "class_hierarchy": class inheritance tree
                - "file_symbols": all symbols defined in a file
                - "doc_structure": markdown documentation structure
                - "full_overview": high-level overview of the codebase
                - "layers": architectural layer-to-layer dependency graph
            file_path: filter to a specific file (optional, for file_imports/file_symbols)
            symbol_name: filter to a specific symbol (optional, for call_graph/class_hierarchy)
            max_nodes: max nodes to include (default 30)
            format: "mermaid" (default) or "dot" (Graphviz DOT)

        Returns the diagram source and a rendering hint.
        """
        conn = _get_conn()
        diagram = ""

        root = _srv._root
        generators = {
            "file_imports": lambda: viz_file_imports(
                conn, root, file_path, max_nodes, format
            ),
            "call_graph": lambda: viz_call_graph(
                conn, root, symbol_name, max_nodes, format
            ),
            "class_hierarchy": lambda: viz_class_hierarchy(
                conn, root, symbol_name, max_nodes, format
            ),
            "file_symbols": lambda: viz_file_symbols(conn, root, file_path, format),
            "doc_structure": lambda: viz_doc_structure(
                conn, root, file_path, max_nodes, format
            ),
            "full_overview": lambda: viz_full_overview(conn, root, max_nodes, format),
            "layers": lambda: viz_layers(conn, root, format),
        }
        generator = generators.get(scope)
        if generator is None:
            return json.dumps({"error": f"Unknown scope: {scope}"})
        diagram = generator()

        return json.dumps(
            {
                "scope": scope,
                "format": format,
                "diagram": diagram,
                "render_hint": (
                    "Paste into https://mermaid.live or any Mermaid renderer"
                    if format == "mermaid"
                    else "Render with: dot -Tsvg graph.dot -o graph.svg"
                ),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def graph_stats() -> str:
        """
        Return counts of all node types in the index.
        Useful to confirm the index is populated.
        """
        conn = _get_conn()
        stats = {
            label: conn.count_nodes(label)
            for label in (
                "File",
                "Function",
                "Class",
                "TFResource",
                "TFVar",
                "MdSection",
            )
        }
        return json.dumps(stats, indent=2)

    @mcp.tool()
    @_logged_tool
    def live_graph_stats() -> str:
        """
        Lightweight snapshot of the index: node counts, FTS symbol count,
        scan freshness (git HEAD vs indexed), and a timestamp. Designed
        for polling, call it repeatedly to watch the graph change during
        an ongoing scan or watcher burst.

        Differs from graph_stats: also includes scan_status and FTS count
        so you don't need multiple tool calls to assess the index health.
        """
        from codegraph.core.fts import get_fts_conn
        from codegraph.state.scan_meta import scan_status as _scan_status

        conn = _get_conn()
        nodes: dict[str, int] = {}
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            try:
                nodes[label] = conn.count_nodes(label)
            except Exception:
                nodes[label] = 0

        fts_count = 0
        try:
            fts_conn = get_fts_conn(_srv._root)
            fts_count = fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        except Exception:
            pass

        ss = {}
        try:
            if _srv._root is not None:
                ss = _scan_status(_srv._root)
                # Trim changed_files for token economy in polling
                if isinstance(ss.get("changed_files"), list):
                    n = len(ss["changed_files"])
                    if n > 20:
                        ss["changed_files"] = ss["changed_files"][:20]
                        ss["changed_files_total"] = n
        except Exception:
            pass

        import time as _t

        return json.dumps(
            {
                "nodes": nodes,
                "nodes_total": sum(nodes.values()),
                "fts_symbols": fts_count,
                "scan": ss,
                "sampled_at": _t.time(),
            },
            indent=2,
        )
