# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP visualization tools — visualize_graph, graph_stats.
#              Internal helpers generate Mermaid/DOT diagrams using _root-aware
#              _short_path from the server package.

from __future__ import annotations

import json
import os

from codegraph.core.utils import rows as _rows
from codegraph.core.utils import safe_id as _safe_id


def register(mcp) -> None:
    """Register visualization tools on the given FastMCP instance."""
    from codegraph.server import _get_conn, _logged_tool, _root, _short_path

    # -------------------------------------------------------------------
    # Internal diagram generators (use _short_path which depends on _root)
    # -------------------------------------------------------------------

    def _viz_file_imports(conn, file_path: str, max_nodes: int, fmt: str) -> str:
        if file_path:
            if not os.path.isabs(file_path) and _root:
                file_path = str(_root / file_path)
            r = conn.execute(
                """MATCH (src:File {path:$p})-[:IMPORTS]->(dep:File)
                   RETURN src.path AS src, dep.path AS tgt
                   UNION ALL
                   MATCH (up:File)-[:IMPORTS]->(src:File {path:$p})
                   RETURN up.path AS src, src.path AS tgt""",
                {"p": file_path},
            )
        else:
            r = conn.execute(
                f"MATCH (src:File)-[:IMPORTS]->(tgt:File) "
                f"RETURN src.path AS src, tgt.path AS tgt LIMIT {max_nodes * 2}",
            )

        rows = _rows(r)
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
        if symbol_name:
            r = conn.execute(
                """MATCH (caller:Function)-[:CALLS]->(callee:Function)
                   WHERE caller.name = $n OR callee.name = $n
                   RETURN caller.name AS src, callee.name AS tgt,
                          caller.file_path AS src_file, callee.file_path AS tgt_file
                   LIMIT $lim""",
                {"n": symbol_name, "lim": max_nodes * 2},
            )
        else:
            r = conn.execute(
                f"MATCH (caller:Function)-[:CALLS]->(callee:Function) "
                f"RETURN caller.name AS src, callee.name AS tgt, "
                f"caller.file_path AS src_file, callee.file_path AS tgt_file LIMIT {max_nodes * 2}",
            )

        rows = _rows(r)
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
        if symbol_name:
            r = conn.execute(
                """MATCH (child:Class)-[:INHERITS]->(parent:Class)
                   WHERE child.name = $n OR parent.name = $n
                   RETURN child.name AS child, parent.name AS parent,
                          child.file_path AS child_file
                   LIMIT $lim""",
                {"n": symbol_name, "lim": max_nodes},
            )
        else:
            r = conn.execute(
                f"MATCH (child:Class)-[:INHERITS]->(parent:Class) "
                f"RETURN child.name AS child, parent.name AS parent, "
                f"child.file_path AS child_file LIMIT {max_nodes}",
            )

        rows = _rows(r)
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

        if not os.path.isabs(file_path) and _root:
            file_path = str(_root / file_path)

        short = _short_path(file_path)
        file_id = _safe_id(short)

        # Functions
        r_fn = conn.execute(
            "MATCH (fn:Function) WHERE fn.file_path = $p RETURN fn.name, fn.start_line ORDER BY fn.start_line",
            {"p": file_path},
        )
        fns = _rows(r_fn)

        # Classes
        r_cls = conn.execute(
            "MATCH (c:Class) WHERE c.file_path = $p RETURN c.name, c.start_line ORDER BY c.start_line",
            {"p": file_path},
        )
        classes = _rows(r_cls)

        # MdSections
        r_md = conn.execute(
            "MATCH (s:MdSection) WHERE s.file_path = $p RETURN s.title, s.level, s.start_line ORDER BY s.start_line",
            {"p": file_path},
        )
        sections = _rows(r_md)

        if fmt == "mermaid":
            lines = ["graph TD", f'  {file_id}["{short}"]:::file']
            for cls in classes:
                cls_id = _safe_id(f"cls_{cls['c.name']}")
                lines.append(f'  {cls_id}["{cls["c.name"]} (L{cls["c.start_line"]})"]:::class')
                lines.append(f"  {file_id} --> {cls_id}")
            for fn in fns:
                fn_id = _safe_id(f"fn_{fn['fn.name']}_{fn['fn.start_line']}")
                lines.append(f'  {fn_id}["{fn["fn.name"]}() L{fn["fn.start_line"]}"]:::func')
                lines.append(f"  {file_id} --> {fn_id}")
            for sec in sections:
                sec_id = _safe_id(f"sec_{sec['s.title']}_{sec['s.start_line']}")
                prefix = "#" * sec["s.level"]
                lines.append(f'  {sec_id}["{prefix} {sec["s.title"]} L{sec["s.start_line"]}"]:::doc')
                lines.append(f"  {file_id} --> {sec_id}")
            lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
            lines.append("  classDef class fill:#fff3e0,stroke:#f57c00")
            lines.append("  classDef func fill:#e8f5e9,stroke:#388e3c")
            lines.append("  classDef doc fill:#fce4ec,stroke:#c62828")
            return "\n".join(lines)
        else:
            lines = ["digraph file_symbols {", "  rankdir=TD;", f'  "{short}" [shape=folder];']
            for cls in classes:
                lines.append(f'  "{cls["c.name"]}" [shape=box,style=filled,fillcolor=lightyellow];')
                lines.append(f'  "{short}" -> "{cls["c.name"]}";')
            for fn in fns:
                lines.append(f'  "{fn["fn.name"]}" [shape=ellipse];')
                lines.append(f'  "{short}" -> "{fn["fn.name"]}";')
            lines.append("}")
            return "\n".join(lines)

    def _viz_doc_structure(conn, file_path: str, max_nodes: int, fmt: str) -> str:
        if file_path:
            if not os.path.isabs(file_path) and _root:
                file_path = str(_root / file_path)
            r = conn.execute(
                "MATCH (s:MdSection) WHERE s.file_path = $p "
                "RETURN s.id, s.title, s.level, s.start_line, s.file_path "
                "ORDER BY s.start_line LIMIT $lim",
                {"p": file_path, "lim": max_nodes},
            )
        else:
            r = conn.execute(
                f"MATCH (s:MdSection) "
                f"RETURN s.id, s.title, s.level, s.start_line, s.file_path "
                f"ORDER BY s.file_path, s.start_line LIMIT {max_nodes}",
            )

        rows = _rows(r)
        if not rows:
            return "graph TD\n  NO_DOCS[No markdown sections found]"

        if fmt == "mermaid":
            lines = ["graph TD"]
            by_file: dict[str, list] = {}
            for row in rows:
                fp = _short_path(row["s.file_path"])
                by_file.setdefault(fp, []).append(row)

            for fp, secs in by_file.items():
                fp_id = _safe_id(fp)
                lines.append(f'  {fp_id}["{fp}"]:::file')
                prev_by_level: dict[int, str] = {}
                for sec in secs:
                    sec_id = _safe_id(f"s_{sec['s.start_line']}_{fp}")
                    prefix = "#" * sec["s.level"]
                    lines.append(f'  {sec_id}["{prefix} {sec["s.title"]}"]:::h{min(sec["s.level"], 3)}')
                    parent_id = None
                    for lvl in range(sec["s.level"] - 1, 0, -1):
                        if lvl in prev_by_level:
                            parent_id = prev_by_level[lvl]
                            break
                    if parent_id:
                        lines.append(f"  {parent_id} --> {sec_id}")
                    else:
                        lines.append(f"  {fp_id} --> {sec_id}")
                    prev_by_level[sec["s.level"]] = sec_id

            lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
            lines.append("  classDef h1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px")
            lines.append("  classDef h2 fill:#fff9c4,stroke:#f9a825")
            lines.append("  classDef h3 fill:#ffecb3,stroke:#ff8f00")
            return "\n".join(lines)
        else:
            lines = ["digraph docs {", "  rankdir=TD;"]
            for row in rows:
                fp = _short_path(row["s.file_path"])
                lines.append(f'  "{row["s.title"]}" [label="{"#" * row["s.level"]} {row["s.title"]}"];')
            lines.append("}")
            return "\n".join(lines)

    def _viz_full_overview(conn, max_nodes: int, fmt: str) -> str:
        """High-level codebase overview: files grouped by language with counts."""
        r = conn.execute(
            "MATCH (f:File) RETURN f.lang AS lang, count(f) AS cnt ORDER BY cnt DESC",
        )
        lang_stats = _rows(r)

        fn_rows = _rows(conn.execute("MATCH (n:Function) RETURN count(n) AS c"))
        fn_count = fn_rows[0]["c"] if fn_rows else 0

        cls_rows = _rows(conn.execute("MATCH (n:Class) RETURN count(n) AS c"))
        cls_count = cls_rows[0]["c"] if cls_rows else 0

        md_rows = _rows(conn.execute("MATCH (n:MdSection) RETURN count(n) AS c"))
        md_count = md_rows[0]["c"] if md_rows else 0

        # Top files by symbol density
        r5 = conn.execute(
            "MATCH (f:File)-[:DEFINES_FN]->(fn:Function) "
            "RETURN f.path AS file, f.lang AS lang, count(fn) AS fn_count "
            "ORDER BY fn_count DESC LIMIT $lim",
            {"lim": max_nodes},
        )
        top_files = _rows(r5)

        if fmt == "mermaid":
            lines = ["graph TD"]
            lines.append(f'  REPO["{_root.name}"]:::repo')

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
            lines = ["digraph overview {", "  rankdir=TD;", f'  repo [label="{_root.name}",shape=box3d];']
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
        stats = {}
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            # Kuzu Cypher requires literal labels — safe: fixed allowlist
            query = "MATCH (n:" + label + ") RETURN count(n) AS c"
            r = conn.execute(query)
            rows = _rows(r)
            stats[label] = rows[0]["c"] if rows else 0
        return json.dumps(stats, indent=2)

    @mcp.tool()
    @_logged_tool
    def live_graph_stats() -> str:
        """
        Lightweight snapshot of the index: node counts, FTS symbol count,
        scan freshness (git HEAD vs indexed), and a timestamp. Designed
        for polling — call it repeatedly to watch the graph change during
        an ongoing scan or watcher burst.

        Differs from graph_stats: also includes scan_status and FTS count
        so you don't need multiple tool calls to assess the index health.
        """
        from codegraph.fts import get_fts_conn
        from codegraph.scan_meta import scan_status as _scan_status

        conn = _get_conn()
        nodes: dict[str, int] = {}
        for label in ("File", "Function", "Class", "TFResource", "TFVar", "MdSection"):
            try:
                query = "MATCH (n:" + label + ") RETURN count(n) AS c"
                r = conn.execute(query)
                rows = _rows(r)
                nodes[label] = rows[0]["c"] if rows else 0
            except Exception:
                nodes[label] = 0

        fts_count = 0
        try:
            fts_conn = get_fts_conn(_root)
            fts_count = fts_conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        except Exception:
            pass

        ss = {}
        try:
            if _root is not None:
                ss = _scan_status(_root)
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
