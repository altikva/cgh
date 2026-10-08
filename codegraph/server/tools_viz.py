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
        Can I see these relationships as a diagram?
        Returns Mermaid (or DOT) source for imports, a call graph, a class
        tree, a file's symbols, docs or layers; see `scope`. For a human to
        look at; to answer a question yourself the query tools are
        cheaper.

        Args:
            scope: what to visualize:
                - "explore": whole-graph JSON payload for the interactive
                  browser view (max_nodes caps the functions carried, by
                  call degree; files are never capped)
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
        if scope == "explore":
            # The interactive `cgh graph` view: whole-graph payload, not a
            # diagram. The CLI asks for it here whenever an owner is running,
            # because the owner holds the write lock.
            from codegraph.viz.graphdata import build_graph_payload

            return json.dumps(
                {
                    "scope": "explore",
                    "format": "json",
                    "payload": build_graph_payload(conn, root, max_nodes),
                }
            )

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
        Is the index populated?
        Returns node counts per type. Edge counts and freshness too:
        live_graph_stats. Staleness against git: scan_status.
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
        How big and how fresh is the index right now?
        Node counts, `edges` per type (non-empty only), FTS symbol count and
        scan freshness in one cheap call, built for polling during a scan.
        Node counts only: graph_stats.
        """
        from codegraph.core.fts import get_fts_conn
        from codegraph.core.graph_model import STATS_EDGE_TYPES, STATS_NODE_LABELS
        from codegraph.state.scan_meta import scan_status as _scan_status

        conn = _get_conn()
        nodes: dict[str, int] = {}
        for label in STATS_NODE_LABELS:
            try:
                nodes[label] = conn.count_nodes(label)
            except Exception:
                nodes[label] = 0
        edges: dict[str, int] = {}
        for edge_type in STATS_EDGE_TYPES:
            try:
                count = conn.count_edges(edge_type)
            except Exception:
                continue
            if count > 0:
                edges[edge_type] = count

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
                "edges": edges,
                "fts_symbols": fts_count,
                "scan": ss,
                "sampled_at": _t.time(),
            },
            indent=2,
        )
