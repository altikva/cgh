# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Mermaid diagram generators for code graph visualization.
#              Consolidated from server.py viz helpers and visualize.py.

from __future__ import annotations

from pathlib import Path

from codegraph.core.utils import rows as _rows
from codegraph.core.utils import safe_id as _safe_id
from codegraph.core.utils import short_path as _short_path

# ---------------------------------------------------------------------------
# Mermaid generators (from visualize.py — used by CLI cmd_graph)
# ---------------------------------------------------------------------------


def mermaid_imports(conn, root: str, file_path: str = "", max_nodes: int = 40) -> str:
    if file_path:
        r = conn.execute(
            "MATCH (src:File)-[:IMPORTS]->(dep:File) "
            "WHERE src.path ENDS WITH $p OR dep.path ENDS WITH $p "
            "RETURN src.path AS src, dep.path AS tgt LIMIT $lim",
            {"p": file_path, "lim": max_nodes * 2},
        )
    else:
        r = conn.execute(
            "MATCH (src:File)-[:IMPORTS]->(dep:File) RETURN src.path AS src, dep.path AS tgt LIMIT $lim",
            {"lim": max_nodes * 2},
        )
    rows = _rows(r)
    if not rows:
        return "graph LR\n  NO_IMPORTS[No import edges found]"

    lines = ["graph LR"]
    seen = set()
    for row in rows:
        src = _short_path(row["src"], root)
        tgt = _short_path(row["tgt"], root)
        src_id, tgt_id = _safe_id(src), _safe_id(tgt)
        if src_id not in seen:
            lines.append(f'  {src_id}["{src}"]')
            seen.add(src_id)
        if tgt_id not in seen:
            lines.append(f'  {tgt_id}["{tgt}"]')
            seen.add(tgt_id)
        lines.append(f"  {src_id} --> {tgt_id}")
    return "\n".join(lines)


def mermaid_calls(conn, root: str, symbol: str = "", max_nodes: int = 40) -> str:
    if symbol:
        r = conn.execute(
            "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
            "WHERE caller.name = $n OR callee.name = $n "
            "RETURN caller.name AS src, callee.name AS tgt, "
            "caller.file_path AS sf, callee.file_path AS tf LIMIT $lim",
            {"n": symbol, "lim": max_nodes * 2},
        )
    else:
        r = conn.execute(
            "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
            "RETURN caller.name AS src, callee.name AS tgt, "
            "caller.file_path AS sf, callee.file_path AS tf LIMIT $lim",
            {"lim": max_nodes * 2},
        )
    rows = _rows(r)
    if not rows:
        return "graph LR\n  NO_CALLS[No call edges found]"

    lines = ["graph LR"]
    seen = set()
    for row in rows:
        src, tgt = row["src"], row["tgt"]
        src_id, tgt_id = _safe_id(src), _safe_id(tgt)
        if src_id not in seen:
            sf = _short_path(row["sf"], root)
            lines.append(f'  {src_id}["{src}<br/><small>{sf}</small>"]')
            seen.add(src_id)
        if tgt_id not in seen:
            tf = _short_path(row["tf"], root)
            lines.append(f'  {tgt_id}["{tgt}<br/><small>{tf}</small>"]')
            seen.add(tgt_id)
        lines.append(f"  {src_id} --> {tgt_id}")
    return "\n".join(lines)


def mermaid_classes(conn, root: str, symbol: str = "", max_nodes: int = 40) -> str:
    if symbol:
        r = conn.execute(
            "MATCH (child:Class)-[:INHERITS]->(parent:Class) "
            "WHERE child.name = $n OR parent.name = $n "
            "RETURN child.name AS child, parent.name AS parent, "
            "child.file_path AS cf LIMIT $lim",
            {"n": symbol, "lim": max_nodes},
        )
    else:
        r = conn.execute(
            "MATCH (child:Class)-[:INHERITS]->(parent:Class) "
            "RETURN child.name AS child, parent.name AS parent, "
            "child.file_path AS cf LIMIT $lim",
            {"lim": max_nodes},
        )
    rows = _rows(r)
    if not rows:
        return "graph BT\n  NO_INHERITANCE[No class hierarchy found]"

    lines = ["graph BT"]
    seen = set()
    for row in rows:
        child, parent = row["child"], row["parent"]
        cid, pid = _safe_id(child), _safe_id(parent)
        if cid not in seen:
            lines.append(f'  {cid}["{child}"]')
            seen.add(cid)
        if pid not in seen:
            lines.append(f'  {pid}["{parent}"]:::base')
            seen.add(pid)
        lines.append(f"  {cid} -->|extends| {pid}")
    lines.append("  classDef base fill:#f9f,stroke:#333,stroke-width:2px")
    return "\n".join(lines)


def mermaid_docs(conn, root: str, file_path: str = "", max_nodes: int = 40) -> str:
    if file_path:
        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.file_path ENDS WITH $p "
            "RETURN s.title, s.level, s.start_line, s.file_path "
            "ORDER BY s.start_line LIMIT $lim",
            {"p": file_path, "lim": max_nodes},
        )
    else:
        r = conn.execute(
            "MATCH (s:MdSection) "
            "RETURN s.title, s.level, s.start_line, s.file_path "
            "ORDER BY s.file_path, s.start_line LIMIT $lim",
            {"lim": max_nodes},
        )
    rows = _rows(r)
    if not rows:
        return "graph TD\n  NO_DOCS[No doc sections found]"

    lines = ["graph TD"]
    by_file: dict[str, list] = {}
    for row in rows:
        fp = _short_path(row["s.file_path"], root)
        by_file.setdefault(fp, []).append(row)

    for fp, secs in by_file.items():
        fp_id = _safe_id(fp)
        lines.append(f'  {fp_id}["{fp}"]:::file')
        prev: dict[int, str] = {}
        for sec in secs:
            sid = _safe_id(f"s_{sec['s.start_line']}_{fp}")
            h = "#" * sec["s.level"]
            lines.append(f'  {sid}["{h} {sec["s.title"]}"]:::h{min(sec["s.level"], 3)}')
            parent_id = None
            for lvl in range(sec["s.level"] - 1, 0, -1):
                if lvl in prev:
                    parent_id = prev[lvl]
                    break
            lines.append(f"  {parent_id or fp_id} --> {sid}")
            prev[sec["s.level"]] = sid

    lines.append("  classDef file fill:#e1f5fe,stroke:#0288d1")
    lines.append("  classDef h1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px")
    lines.append("  classDef h2 fill:#fff9c4,stroke:#f9a825")
    lines.append("  classDef h3 fill:#ffecb3,stroke:#ff8f00")
    return "\n".join(lines)


def mermaid_overview(conn, root: str, max_nodes: int = 30) -> str:
    r = conn.execute(
        "MATCH (f:File) RETURN f.lang AS lang, count(f) AS cnt ORDER BY cnt DESC",
    )
    lang_stats = _rows(r)

    counts = {}
    for label in ("Function", "Class", "MdSection"):
        # Kuzu Cypher requires literal labels — safe: fixed allowlist
        query = "MATCH (n:" + label + ") RETURN count(n) AS c"
        r2 = conn.execute(query)
        for row in _rows(r2):
            counts[label] = row["c"]

    repo_name = Path(root).name
    lines = ["graph TD"]
    lines.append(f'  REPO["{repo_name}"]:::repo')

    for ls in lang_stats:
        lid = _safe_id(ls["lang"] or "unknown")
        lines.append(f'  {lid}["{ls["lang"] or "other"}: {ls["cnt"]} files"]:::lang')
        lines.append(f"  REPO --> {lid}")

    fn = counts.get("Function", 0)
    cls = counts.get("Class", 0)
    md = counts.get("MdSection", 0)
    lines.append(f'  STATS["{fn} functions | {cls} classes | {md} doc sections"]:::stats')
    lines.append("  REPO --> STATS")

    # Top files
    r3 = conn.execute(
        "MATCH (f:File)-[:DEFINES_FN]->(fn:Function) "
        "RETURN f.path AS file, f.lang AS lang, count(fn) AS fn_count "
        "ORDER BY fn_count DESC LIMIT $lim",
        {"lim": min(max_nodes, 10)},
    )
    for row in _rows(r3):
        short = _short_path(row["file"], root)
        fid = _safe_id(short)
        lid = _safe_id(row["lang"] or "unknown")
        lines.append(f'  {fid}["{short}<br/>{row["fn_count"]} fns"]:::hot')
        lines.append(f"  {lid} --> {fid}")

    lines.append("  classDef repo fill:#1a237e,color:#fff,stroke-width:2px")
    lines.append("  classDef lang fill:#e8eaf6,stroke:#3f51b5")
    lines.append("  classDef stats fill:#f3e5f5,stroke:#7b1fa2")
    lines.append("  classDef hot fill:#fff3e0,stroke:#e65100")
    return "\n".join(lines)
