# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The graph visualization generators, one implementation
#              for both entry points (the visualize_graph MCP tool and
#              `cgh graph`). Every generator speaks the GraphDB
#              protocol only, so DuckDB and Kuzu render identically;
#              the raw-Cypher ancestors in viz/mermaid.py were
#              Kuzu-only and are retired. Each function takes the repo
#              root explicitly (no server-global reach-through).

from __future__ import annotations

import os

from codegraph.core.utils import safe_id as _safe_id
from codegraph.core.utils import short_path as _short_base


def _short(root, path: str) -> str:
    """Shorten a path for display relative to the repo root."""
    return _short_base(path, str(root)) if root else path


def viz_file_imports(conn, root, file_path: str, max_nodes: int, fmt: str) -> str:
    if file_path:
        if not os.path.isabs(file_path) and root:
            file_path = str(root / file_path)
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
        rows = [
            {"src": r["src_path"], "tgt": r["dst_path"]} for r in outgoing + incoming
        ]
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
            src = _short(root, row["src"])
            tgt = _short(root, row["tgt"])
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
            src = _short(root, row["src"])
            tgt = _short(root, row["tgt"])
            lines.append(f'  "{src}" -> "{tgt}";')
        lines.append("}")
        return "\n".join(lines)


def viz_call_graph(conn, root, symbol_name: str, max_nodes: int, fmt: str) -> str:
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
            "CALLS",
            **return_args,
            limit=max_nodes * 2,
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
                src_label = f"{src}\\n{_short(root, row['src_file'])}"
                lines.append(f'  {src_id}["{src_label}"]')
                seen.add(src_id)
            if tgt_id not in seen:
                tgt_label = f"{tgt}\\n{_short(root, row['tgt_file'])}"
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


def viz_class_hierarchy(conn, root, symbol_name: str, max_nodes: int, fmt: str) -> str:
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
            "INHERITS",
            **return_args,
            limit=max_nodes,
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


def viz_file_symbols(conn, root, file_path: str, fmt: str) -> str:
    if not file_path:
        return "graph TD\n  ERR[file_path required for file_symbols scope]"

    if not os.path.isabs(file_path) and root:
        file_path = str(root / file_path)

    short = _short(root, file_path)
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
            lines.append(
                f'  {sec_id}["{prefix} {sec["title"]} L{sec["start_line"]}"]:::doc'
            )
            lines.append(f"  {file_id} --> {sec_id}")
        lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
        lines.append("  classDef class fill:#fff3e0,stroke:#f57c00")
        lines.append("  classDef func fill:#e8f5e9,stroke:#388e3c")
        lines.append("  classDef doc fill:#fce4ec,stroke:#c62828")
        return "\n".join(lines)
    else:
        lines = [
            "digraph file_symbols {",
            "  rankdir=TD;",
            f'  "{short}" [shape=folder];',
        ]
        for cls in classes:
            lines.append(
                f'  "{cls["name"]}" [shape=box,style=filled,fillcolor=lightyellow];'
            )
            lines.append(f'  "{short}" -> "{cls["name"]}";')
        for fn in fns:
            lines.append(f'  "{fn["name"]}" [shape=ellipse];')
            lines.append(f'  "{short}" -> "{fn["name"]}";')
        lines.append("}")
        return "\n".join(lines)


def viz_doc_structure(conn, root, file_path: str, max_nodes: int, fmt: str) -> str:
    common_fields = ["id", "title", "level", "start_line", "file_path"]
    if file_path:
        if not os.path.isabs(file_path) and root:
            file_path = str(root / file_path)
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
            fp = _short(root, row["file_path"])
            by_file.setdefault(fp, []).append(row)

        for fp, secs in by_file.items():
            fp_id = _safe_id(fp)
            lines.append(f'  {fp_id}["{fp}"]:::file')
            prev_by_level: dict[int, str] = {}
            for sec in secs:
                sec_id = _safe_id(f"s_{sec['start_line']}_{fp}")
                prefix = "#" * sec["level"]
                lines.append(
                    f'  {sec_id}["{prefix} {sec["title"]}"]:::h{min(sec["level"], 3)}'
                )
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


def viz_full_overview(conn, root, max_nodes: int, fmt: str) -> str:
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
    defines = conn.find_neighbors("DEFINES_FN", return_src=["path", "lang"])
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
        lines.append(f'  REPO["{root.name if root else "repo"}"]:::repo')

        for ls in lang_stats:
            lang_id = _safe_id(ls["lang"] or "unknown")
            lines.append(
                f'  {lang_id}["{ls["lang"] or "other"}: {ls["cnt"]} files"]:::lang'
            )
            lines.append(f"  REPO --> {lang_id}")

        lines.append(
            f'  STATS["Functions: {fn_count} | Classes: {cls_count} | Doc sections: {md_count}"]:::stats'
        )
        lines.append("  REPO --> STATS")

        for tf in top_files[:10]:
            short = _short(root, tf["file"])
            tf_id = _safe_id(short)
            lang_id = _safe_id(tf["lang"] or "unknown")
            lines.append(f'  {tf_id}["{short} ({tf["fn_count"]} fns)"]:::hotfile')
            lines.append(f"  {lang_id} --> {tf_id}")

        lines.append(
            "  classDef repo fill:#1a237e,stroke:#fff,color:#fff,stroke-width:2px"
        )
        lines.append("  classDef lang fill:#e8eaf6,stroke:#3f51b5")
        lines.append("  classDef stats fill:#f3e5f5,stroke:#7b1fa2")
        lines.append("  classDef hotfile fill:#fff3e0,stroke:#e65100")
        return "\n".join(lines)
    else:
        lines = [
            "digraph overview {",
            "  rankdir=TD;",
            f'  repo [label="{root.name if root else "repo"}",shape=box3d];',
        ]
        for ls in lang_stats:
            lines.append(f'  "{ls["lang"]}" [label="{ls["lang"]}: {ls["cnt"]} files"];')
            lines.append(f'  repo -> "{ls["lang"]}";')
        lines.append("}")
        return "\n".join(lines)


def viz_layers(conn, root, fmt: str) -> str:
    """Layer-dependency diagram. Reuses the backend-neutral builder in
    viz.mermaid so the CLI `cgh graph layers` and this MCP scope render
    the same thing. For dot we emit the same layer->layer edges."""
    from codegraph.viz.mermaid import _layer_edge_counts, _layer_sort_key

    if fmt == "mermaid":
        from codegraph.viz.mermaid import mermaid_layers

        return mermaid_layers(conn)

    counts = _layer_edge_counts(conn)
    if not counts:
        return 'digraph layers {\n  NO_LAYERS [label="No layer edges"];\n}'
    lines = ["digraph layers {", "  rankdir=TD;"]
    for sl, dl in sorted(
        counts, key=lambda e: (_layer_sort_key(e[0]), _layer_sort_key(e[1]))
    ):
        lines.append(f'  "{sl}" -> "{dl}" [label="{counts[(sl, dl)]}"];')
    lines.append("}")
    return "\n".join(lines)


# -------------------------------------------------------------------
# MCP tool registrations
