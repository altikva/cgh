# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP visualization tools: visualize_graph, graph_stats.
#              Internal helpers generate Mermaid/DOT diagrams using _root-aware
#              _short_path from the server package.

from __future__ import annotations

import json
import os

from codegraph.core.utils import safe_id as _safe_id


def register(mcp) -> None:
    """Register visualization tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _logged_tool, _short_path

    # -------------------------------------------------------------------
    # Internal diagram generators (use _short_path which depends on _root)
    # -------------------------------------------------------------------

    def _viz_file_imports(conn, file_path: str, max_nodes: int, fmt: str) -> str:
        if file_path:
            if not os.path.isabs(file_path) and _srv._root:
                file_path = str(_srv._root / file_path)
            # Outgoing imports (file imports X) + incoming imports (X imports file)
            outgoing = conn.find_neighbors(
                "IMPORTS",
                src_key=file_path,
                return_src=["path"],
                return_dst=["path"],
            )
            incoming = conn.find_neighbors(
                "IMPORTS",
                dst_key=file_path,
                return_src=["path"],
                return_dst=["path"],
            )
            rows = [{"src": r["src_path"], "tgt": r["dst_path"]} for r in outgoing + incoming]
        else:
            rows = [
                {"src": r["src_path"], "tgt": r["dst_path"]}
                for r in conn.find_neighbors(
                    "IMPORTS",
                    return_src=["path"],
                    return_dst=["path"],
                    limit=max_nodes * 2,
                )
            ]

        if not rows:
            return "graph LR\n  NO_IMPORTS[No import edges found]"

        if fmt == "mermaid":
            lines = ["graph LR"]
            seen_nodes = set()
            for row in rows[: max_nodes * 2]:
                src = _short_path(row["src"])
                tgt = _short_path(row["tgt"])
                src_id = _safe_id(src)
                tgt_id = _safe_id(tgt)
                if src_id not in seen_nodes:
                    lines.append(f'  {src_id}["{src}"]')
                    seen_nodes.add(src_id)
                if tgt_id not in seen_nodes:
                    lines.append(f'  {tgt_id}["{tgt}"]')
                    seen_nodes.add(tgt_id)
                lines.append(f"  {src_id} --> {tgt_id}")
            return "\n".join(lines)
        else:
            lines = ["digraph imports {", "  rankdir=LR;"]
            for row in rows[: max_nodes * 2]:
                src = _short_path(row["src"])
                tgt = _short_path(row["tgt"])
                lines.append(f'  "{src}" -> "{tgt}";')
            lines.append("}")
            return "\n".join(lines)

    def _viz_call_graph(conn, symbol_name: str, max_nodes: int, fmt: str) -> str:
        return_args = dict(
            return_src=["name", "file_path"],
            return_dst=["name", "file_path"],
        )
        if symbol_name:
            # Cypher's "WHERE caller.name = $n OR callee.name = $n" → two
            # filtered queries unioned in Python. Most names point to a
            # handful of edges, so the duplication is cheap.
            edges_a = conn.find_neighbors(
                "CALLS",
                src_where={"name": symbol_name},
                **return_args,
                limit=max_nodes * 2,
            )
            edges_b = conn.find_neighbors(
                "CALLS",
                dst_where={"name": symbol_name},
                **return_args,
                limit=max_nodes * 2,
            )
            edges = edges_a + edges_b
        else:
            edges = conn.find_neighbors(
                "CALLS", **return_args, limit=max_nodes * 2,
            )

        rows = [
            {
                "src": r["src_name"],
                "tgt": r["dst_name"],
                "src_file": r["src_file_path"],
                "tgt_file": r["dst_file_path"],
            }
            for r in edges
        ]
        if not rows:
            return "graph LR\n  NO_CALLS[No call edges found]"

        if fmt == "mermaid":
            lines = ["graph LR"]
            seen = set()
            for row in rows:
                src, tgt = row["src"], row["tgt"]
                src_id, tgt_id = _safe_id(src), _safe_id(tgt)
                if src_id not in seen:
                    src_label = f"{src}\\n{_short_path(row['src_file'])}"
                    lines.append(f'  {src_id}["{src_label}"]')
                    seen.add(src_id)
                if tgt_id not in seen:
                    tgt_label = f"{tgt}\\n{_short_path(row['tgt_file'])}"
                    lines.append(f'  {tgt_id}["{tgt_label}"]')
                    seen.add(tgt_id)
                lines.append(f"  {src_id} --> {tgt_id}")
            return "\n".join(lines)
        else:
            lines = ["digraph calls {", "  rankdir=LR;"]
            for row in rows:
                lines.append(f'  "{row["src"]}" -> "{row["tgt"]}";')
            lines.append("}")
            return "\n".join(lines)

    def _viz_class_hierarchy(conn, symbol_name: str, max_nodes: int, fmt: str) -> str:
        return_args = dict(
            return_src=["name", "file_path"],
            return_dst=["name"],
        )
        if symbol_name:
            edges_a = conn.find_neighbors(
                "INHERITS",
                src_where={"name": symbol_name},
                **return_args,
                limit=max_nodes,
            )
            edges_b = conn.find_neighbors(
                "INHERITS",
                dst_where={"name": symbol_name},
                **return_args,
                limit=max_nodes,
            )
            edges = edges_a + edges_b
        else:
            edges = conn.find_neighbors(
                "INHERITS", **return_args, limit=max_nodes,
            )

        rows = [
            {
                "child": r["src_name"],
                "parent": r["dst_name"],
                "child_file": r["src_file_path"],
            }
            for r in edges
        ]
        if not rows:
            return "graph BT\n  NO_INHERITANCE[No inheritance edges found]"

        if fmt == "mermaid":
            lines = ["graph BT"]
            seen = set()
            for row in rows:
                child, parent = row["child"], row["parent"]
                child_id, parent_id = _safe_id(child), _safe_id(parent)
                if child_id not in seen:
                    lines.append(f'  {child_id}["{child}"]')
                    seen.add(child_id)
                if parent_id not in seen:
                    lines.append(f'  {parent_id}["{parent}"]:::base')
                    seen.add(parent_id)
                lines.append(f"  {child_id} --> {parent_id}")
            lines.append("  classDef base fill:#f9f,stroke:#333")
            return "\n".join(lines)
        else:
            lines = ["digraph hierarchy {", "  rankdir=BT;"]
            for row in rows:
                lines.append(f'  "{row["child"]}" -> "{row["parent"]}";')
            lines.append("}")
            return "\n".join(lines)

    def _viz_file_symbols(conn, file_path: str, fmt: str) -> str:
        if not file_path:
            return "graph TD\n  ERR[file_path required for file_symbols scope]"

        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        short = _short_path(file_path)
        file_id = _safe_id(short)

        fns = conn.find_nodes(
            "Function",
            where={"file_path": file_path},
            return_fields=["name", "start_line"],
            order_by=["start_line"],
        )
        classes = conn.find_nodes(
            "Class",
            where={"file_path": file_path},
            return_fields=["name", "start_line"],
            order_by=["start_line"],
        )
        sections = conn.find_nodes(
            "MdSection",
            where={"file_path": file_path},
            return_fields=["title", "level", "start_line"],
            order_by=["start_line"],
        )

        if fmt == "mermaid":
            lines = ["graph TD", f'  {file_id}["{short}"]:::file']
            for cls in classes:
                cls_id = _safe_id(f"cls_{cls['name']}")
                lines.append(f'  {cls_id}["{cls["name"]} (L{cls["start_line"]})"]:::class')
                lines.append(f"  {file_id} --> {cls_id}")
            for fn in fns:
                fn_id = _safe_id(f"fn_{fn['name']}_{fn['start_line']}")
                lines.append(f'  {fn_id}["{fn["name"]}() L{fn["start_line"]}"]:::func')
                lines.append(f"  {file_id} --> {fn_id}")
            for sec in sections:
                sec_id = _safe_id(f"sec_{sec['title']}_{sec['start_line']}")
                prefix = "#" * sec["level"]
                lines.append(f'  {sec_id}["{prefix} {sec["title"]} L{sec["start_line"]}"]:::doc')
                lines.append(f"  {file_id} --> {sec_id}")
            lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
            lines.append("  classDef class fill:#fff3e0,stroke:#f57c00")
            lines.append("  classDef func fill:#e8f5e9,stroke:#388e3c")
            lines.append("  classDef doc fill:#fce4ec,stroke:#c62828")
            return "\n".join(lines)
        else:
            lines = ["digraph file_symbols {", "  rankdir=TD;", f'  "{short}" [shape=folder];']
            for cls in classes:
                lines.append(f'  "{cls["name"]}" [shape=box,style=filled,fillcolor=lightyellow];')
                lines.append(f'  "{short}" -> "{cls["name"]}";')
            for fn in fns:
                lines.append(f'  "{fn["name"]}" [shape=ellipse];')
                lines.append(f'  "{short}" -> "{fn["name"]}";')
            lines.append("}")
            return "\n".join(lines)

    def _viz_doc_structure(conn, file_path: str, max_nodes: int, fmt: str) -> str:
        common_fields = ["id", "title", "level", "start_line", "file_path"]
        if file_path:
            if not os.path.isabs(file_path) and _srv._root:
                file_path = str(_srv._root / file_path)
            rows = conn.find_nodes(
                "MdSection",
                where={"file_path": file_path},
                return_fields=common_fields,
                order_by=["start_line"],
                limit=max_nodes,
            )
        else:
            rows = conn.find_nodes(
                "MdSection",
                return_fields=common_fields,
                order_by=["file_path", "start_line"],
                limit=max_nodes,
            )

        if not rows:
            return "graph TD\n  NO_DOCS[No markdown sections found]"

        if fmt == "mermaid":
            lines = ["graph TD"]
            by_file: dict[str, list] = {}
            for row in rows:
                fp = _short_path(row["file_path"])
                by_file.setdefault(fp, []).append(row)

            for fp, secs in by_file.items():
                fp_id = _safe_id(fp)
                lines.append(f'  {fp_id}["{fp}"]:::file')
                prev_by_level: dict[int, str] = {}
                for sec in secs:
                    sec_id = _safe_id(f"s_{sec['start_line']}_{fp}")
                    prefix = "#" * sec["level"]
                    lines.append(f'  {sec_id}["{prefix} {sec["title"]}"]:::h{min(sec["level"], 3)}')
                    parent_id = None
                    for lvl in range(sec["level"] - 1, 0, -1):
                        if lvl in prev_by_level:
                            parent_id = prev_by_level[lvl]
                            break
                    if parent_id:
                        lines.append(f"  {parent_id} --> {sec_id}")
                    else:
                        lines.append(f"  {fp_id} --> {sec_id}")
                    prev_by_level[sec["level"]] = sec_id

            lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
            lines.append("  classDef h1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px")
            lines.append("  classDef h2 fill:#fff9c4,stroke:#f9a825")
            lines.append("  classDef h3 fill:#ffecb3,stroke:#ff8f00")
            return "\n".join(lines)
        else:
            lines = ["digraph docs {", "  rankdir=TD;"]
            for row in rows:
                lines.append(
                    f'  "{row["title"]}" [label="{"#" * row["level"]} {row["title"]}"];'
                )
            lines.append("}")
            return "\n".join(lines)

    def _viz_full_overview(conn, max_nodes: int, fmt: str) -> str:
        """High-level codebase overview: files grouped by language with counts."""
        # Aggregate "files per language" in Python, small N, no benefit from
        # GROUP BY at the backend.
        from collections import Counter

        files = conn.find_nodes("File", return_fields=["path", "lang"])
        lang_counts = Counter(f.get("lang") for f in files)
        lang_stats = [
            {"lang": lang, "cnt": cnt}
            for lang, cnt in sorted(lang_counts.items(), key=lambda kv: -kv[1])
        ]

        fn_count = conn.count_nodes("Function")
        cls_count = conn.count_nodes("Class")
        md_count = conn.count_nodes("MdSection")

        # Top files by symbol density: count DEFINES_FN edges per file.
        # find_neighbors gives us (file_path, function_id) pairs; tally.
        defines = conn.find_neighbors(
            "DEFINES_FN", return_src=["path", "lang"]
        )
        fn_per_file: dict[tuple[str, str | None], int] = {}
        for r in defines:
            key = (r["src_path"], r.get("src_lang"))
            fn_per_file[key] = fn_per_file.get(key, 0) + 1
        top_files = sorted(
            (
                {"file": path, "lang": lang, "fn_count": cnt}
                for (path, lang), cnt in fn_per_file.items()
            ),
            key=lambda r: -r["fn_count"],
        )[:max_nodes]

        if fmt == "mermaid":
            lines = ["graph TD"]
            lines.append(f'  REPO["{_srv._root.name if _srv._root else "repo"}"]:::repo')

            for ls in lang_stats:
                lang_id = _safe_id(ls["lang"] or "unknown")
                lines.append(f'  {lang_id}["{ls["lang"] or "other"}: {ls["cnt"]} files"]:::lang')
                lines.append(f"  REPO --> {lang_id}")

            lines.append(f'  STATS["Functions: {fn_count} | Classes: {cls_count} | Doc sections: {md_count}"]:::stats')
            lines.append("  REPO --> STATS")

            for tf in top_files[:10]:
                short = _short_path(tf["file"])
                tf_id = _safe_id(short)
                lang_id = _safe_id(tf["lang"] or "unknown")
                lines.append(f'  {tf_id}["{short} ({tf["fn_count"]} fns)"]:::hotfile')
                lines.append(f"  {lang_id} --> {tf_id}")

            lines.append("  classDef repo fill:#1a237e,stroke:#fff,color:#fff,stroke-width:2px")
            lines.append("  classDef lang fill:#e8eaf6,stroke:#3f51b5")
            lines.append("  classDef stats fill:#f3e5f5,stroke:#7b1fa2")
            lines.append("  classDef hotfile fill:#fff3e0,stroke:#e65100")
            return "\n".join(lines)
        else:
            lines = [
                "digraph overview {",
                "  rankdir=TD;",
                f'  repo [label="{_srv._root.name if _srv._root else "repo"}",shape=box3d];',
            ]
            for ls in lang_stats:
                lines.append(f'  "{ls["lang"]}" [label="{ls["lang"]}: {ls["cnt"]} files"];')
                lines.append(f'  repo -> "{ls["lang"]}";')
            lines.append("}")
            return "\n".join(lines)

    # -------------------------------------------------------------------
    # MCP tool registrations
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
            file_path: filter to a specific file (optional, for file_imports/file_symbols)
            symbol_name: filter to a specific symbol (optional, for call_graph/class_hierarchy)
            max_nodes: max nodes to include (default 30)
            format: "mermaid" (default) or "dot" (Graphviz DOT)

        Returns the diagram source and a rendering hint.
        """
        conn = _get_conn()
        diagram = ""

        if scope == "file_imports":
            diagram = _viz_file_imports(conn, file_path, max_nodes, format)
        elif scope == "call_graph":
            diagram = _viz_call_graph(conn, symbol_name, max_nodes, format)
        elif scope == "class_hierarchy":
            diagram = _viz_class_hierarchy(conn, symbol_name, max_nodes, format)
        elif scope == "file_symbols":
            diagram = _viz_file_symbols(conn, file_path, format)
        elif scope == "doc_structure":
            diagram = _viz_doc_structure(conn, file_path, max_nodes, format)
        elif scope == "full_overview":
            diagram = _viz_full_overview(conn, max_nodes, format)
        else:
            return json.dumps({"error": f"Unknown scope: {scope}"})

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
            for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection")
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
