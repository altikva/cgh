# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Whole-graph payload for the interactive browser view, as one
#              map of views. A repo rarely has every kind of edge: Python
#              repos carry imports and calls, infrastructure repos carry
#              Terraform resources, and documentation lives in section trees.
#              Each view ships its own nodes and edges, so the viewer can open
#              on whichever one actually holds the repo's structure.

from __future__ import annotations

import os
from collections import Counter
from typing import Any

# Per-view caps. Files are never capped: a repo's files are the point of the
# overview. The rest are ranked before cutting, so what remains is the part
# worth looking at.
DEFAULT_MAX_SYMBOLS = 400
DEFAULT_MAX_RESOURCES = 1500
DEFAULT_MAX_SECTIONS = 1200


def _rel(root: str, path: str) -> str:
    if path and path.startswith(root):
        return os.path.relpath(path, root)
    return path


def _group(rel_path: str) -> str:
    """Directory bucket shown next to a node ('codegraph/cli', 'tests')."""
    parts = rel_path.split("/")
    if len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else "(root)"


def _view(label, noun, edge, out_title, in_title, cli, nodes, edges, truncated=0):
    return {
        "label": label,
        "noun": noun,
        "edge": edge,
        "outTitle": out_title,
        "inTitle": in_title,
        "cli": cli,
        "nodes": nodes,
        "edges": [list(pair) for pair in edges],
        "truncated": truncated,
    }


def _file_node(root: str, row: dict, fns: int = 0, classes: int = 0) -> dict:
    rel = _rel(root, row["path"])
    return {
        "k": "file",
        "n": rel.split("/")[-1],
        "p": rel,
        "g": _group(rel),
        "l": row.get("lang") or "",
        "r": row.get("role") or "",
        "y": row.get("layer") or "",
        "f": fns,
        "c": classes,
        "s": 0,
    }


def build_graph_payload(
    conn: Any,
    root: str,
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
    max_resources: int = DEFAULT_MAX_RESOURCES,
    max_sections: int = DEFAULT_MAX_SECTIONS,
) -> dict:
    """Build the payload the interactive graph view renders.

    Keys are short because the payload is inlined in the generated HTML and a
    large repo ships tens of thousands of rows. A node carries: k kind, n name,
    p path, g directory group, l language, r role, y layer, f function count,
    c class count, s start line. Edges are index pairs into the view's nodes.
    """
    root = os.path.abspath(root)

    files = [
        f
        for f in conn.find_nodes(
            "File", return_fields=["path", "lang", "role", "layer"]
        )
        if f.get("path")
    ]
    files.sort(key=lambda f: f["path"])
    file_at = {f["path"]: i for i, f in enumerate(files)}

    functions = conn.find_nodes(
        "Function", return_fields=["id", "name", "file_path", "start_line"]
    )
    classes = conn.find_nodes("Class", return_fields=["id", "file_path"])
    fn_per_file = Counter(r.get("file_path") for r in functions)
    cls_per_file = Counter(r.get("file_path") for r in classes)

    views = {
        "files": _files_view(conn, root, files, file_at, fn_per_file, cls_per_file),
        "symbols": _symbols_view(conn, root, functions, max_symbols),
        "infra": _infra_view(
            conn, root, files, fn_per_file, cls_per_file, max_resources
        ),
        "docs": _docs_view(conn, root, files, fn_per_file, cls_per_file, max_sections),
    }

    return {
        "repo": os.path.basename(root) or root,
        "views": {k: v for k, v in views.items() if v["nodes"]},
        "totals": {
            "files": len(files),
            "functions": len(functions),
            "classes": len(classes),
            "symbols_shown": len(views["symbols"]["nodes"]),
            "resources": len(views["infra"]["nodes"]),
            "sections": len(views["docs"]["nodes"]),
        },
    }


def _files_view(conn, root, files, file_at, fn_per_file, cls_per_file) -> dict:
    nodes = [
        _file_node(
            root, f, fn_per_file.get(f["path"], 0), cls_per_file.get(f["path"], 0)
        )
        for f in files
    ]
    edges = set()
    for e in conn.find_neighbors("IMPORTS", return_src=["path"], return_dst=["path"]):
        src = file_at.get(e.get("src_path"))
        dst = file_at.get(e.get("dst_path"))
        if src is not None and dst is not None and src != dst:
            edges.add((src, dst))
    return _view(
        "Files · imports",
        ["file", "files"],
        ["import", "imports"],
        "Imports",
        "Imported by",
        "cgh graph imports --file {path}",
        nodes,
        sorted(edges),
    )


def _symbols_view(conn, root, functions, max_symbols) -> dict:
    by_id = {r["id"]: r for r in functions if r.get("id")}
    calls = [
        (e.get("src_id"), e.get("dst_id"))
        for e in conn.find_neighbors("CALLS", return_src=["id"], return_dst=["id"])
    ]
    degree: Counter = Counter()
    for src, dst in calls:
        if src in by_id and dst in by_id and src != dst:
            degree[src] += 1
            degree[dst] += 1
    kept = [sym for sym, _ in degree.most_common(max(0, max_symbols))]
    at = {sym: i for i, sym in enumerate(kept)}
    nodes = []
    for sym in kept:
        row = by_id[sym]
        rel = _rel(root, row.get("file_path") or "")
        nodes.append(
            {
                "k": "function",
                "n": row.get("name") or "",
                "p": rel,
                "g": _group(rel),
                "l": "",
                "r": "",
                "y": "",
                "f": 0,
                "c": 0,
                "s": row.get("start_line") or 0,
            }
        )
    edges = sorted({(at[s], at[d]) for s, d in calls if s in at and d in at and s != d})
    return _view(
        "Functions · calls",
        ["function", "functions"],
        ["call", "calls"],
        "Calls",
        "Called by",
        "cgh callers {name}",
        nodes,
        edges,
        truncated=max(0, len(degree) - len(kept)),
    )


def _infra_view(conn, root, files, fn_per_file, cls_per_file, max_resources) -> dict:
    """Terraform resources hanging off the file that declares them.

    TF_DEPENDS is carried too, but most indexes have none: the file-to-resource
    edge is what gives an infrastructure repo a readable shape.
    """
    defines = conn.find_neighbors(
        "DEFINES_RESOURCE", return_src=["path"], return_dst=["id", "name", "type"]
    )
    if not defines:
        return _view(
            "Terraform · resources",
            ["resource", "resources"],
            ["declaration", "declarations"],
            "Declares",
            "Declared in",
            "cgh lookup {name}",
            [],
            [],
        )
    defines.sort(key=lambda e: (e.get("src_path") or "", e.get("dst_id") or ""))
    kept = defines[: max(0, max_resources)]

    nodes: list[dict] = []
    index_of: dict[str, int] = {}
    by_path = {f["path"]: f for f in files}
    edges: list[tuple[int, int]] = []
    for e in kept:
        path = e.get("src_path") or ""
        if path not in index_of:
            row = by_path.get(path) or {"path": path, "lang": "terraform"}
            index_of[path] = len(nodes)
            nodes.append(
                _file_node(
                    root, row, fn_per_file.get(path, 0), cls_per_file.get(path, 0)
                )
            )
        res_id = e.get("dst_id") or ""
        if res_id not in index_of:
            rel = _rel(root, path)
            index_of[res_id] = len(nodes)
            nodes.append(
                {
                    "k": "resource",
                    "n": f"{e.get('dst_type') or ''}.{e.get('dst_name') or ''}".strip(
                        "."
                    ),
                    "p": rel,
                    "g": _group(rel),
                    "l": "terraform",
                    "r": "infra",
                    "y": "",
                    "f": 0,
                    "c": 0,
                    "s": 0,
                }
            )
        edges.append((index_of[path], index_of[res_id]))

    for e in conn.find_neighbors("TF_DEPENDS", return_src=["id"], return_dst=["id"]):
        src = index_of.get(e.get("src_id"))
        dst = index_of.get(e.get("dst_id"))
        if src is not None and dst is not None and src != dst:
            edges.append((src, dst))

    return _view(
        "Terraform · resources",
        ["node", "nodes"],
        ["link", "links"],
        "Declares",
        "Declared in",
        "cgh lookup {name}",
        nodes,
        sorted(set(edges)),
        truncated=max(0, len(defines) - len(kept)),
    )


def _docs_view(conn, root, files, fn_per_file, cls_per_file, max_sections) -> dict:
    """Section trees: the file, its headings, and the nesting between them."""
    # Config files expose their keys through the same section model, which puts
    # .mcp.json and pre-commit config in a view promising documentation. The
    # discriminant is asked for and its absence tolerated: an index built
    # before the column existed answers without it, and everything it holds is
    # treated as documentation rather than dropped.
    try:
        defines = conn.find_neighbors(
            "DEFINES_SECTION",
            return_src=["path"],
            return_dst=["id", "title", "level", "kind"],
        )
    except Exception:
        defines = conn.find_neighbors(
            "DEFINES_SECTION",
            return_src=["path"],
            return_dst=["id", "title", "level"],
        )
    defines = [e for e in defines if (e.get("dst_kind") or "doc") == "doc"]
    if not defines:
        return _view(
            "Docs · sections",
            ["section", "sections"],
            ["link", "links"],
            "Contains",
            "Contained in",
            "cgh outline {path}",
            [],
            [],
        )

    contains = conn.find_neighbors(
        "CONTAINS_SECTION", return_src=["id"], return_dst=["id"]
    )
    child_count: Counter = Counter()
    for e in contains:
        child_count[e.get("src_id")] += 1
    # Keep the top of each tree first: shallow headings, then those with the
    # most children. A 2,000-section repo stays readable.
    defines.sort(
        key=lambda e: (
            e.get("dst_level") or 9,
            -child_count.get(e.get("dst_id"), 0),
            e.get("src_path") or "",
        )
    )
    kept = defines[: max(0, max_sections)]

    nodes: list[dict] = []
    index_of: dict[str, int] = {}
    by_path = {f["path"]: f for f in files}
    edges: list[tuple[int, int]] = []
    for e in kept:
        path = e.get("src_path") or ""
        if path not in index_of:
            row = by_path.get(path) or {"path": path, "lang": "markdown"}
            index_of[path] = len(nodes)
            nodes.append(
                _file_node(
                    root, row, fn_per_file.get(path, 0), cls_per_file.get(path, 0)
                )
            )
        sec_id = e.get("dst_id") or ""
        if sec_id not in index_of:
            rel = _rel(root, path)
            level = e.get("dst_level") or 1
            index_of[sec_id] = len(nodes)
            nodes.append(
                {
                    "k": "section",
                    "n": ("#" * min(level, 6)) + " " + (e.get("dst_title") or ""),
                    "p": rel,
                    "g": _group(rel),
                    "l": "markdown",
                    "r": "doc",
                    "y": "",
                    "f": 0,
                    "c": 0,
                    "s": level,
                }
            )
        edges.append((index_of[path], index_of[sec_id]))

    for e in contains:
        src = index_of.get(e.get("src_id"))
        dst = index_of.get(e.get("dst_id"))
        if src is not None and dst is not None and src != dst:
            edges.append((src, dst))
    for e in conn.find_neighbors("MD_LINKS_TO", return_src=["id"], return_dst=["path"]):
        src = index_of.get(e.get("src_id"))
        dst = index_of.get(e.get("dst_path"))
        if src is not None and dst is not None and src != dst:
            edges.append((src, dst))

    return _view(
        "Docs · sections",
        ["node", "nodes"],
        ["link", "links"],
        "Contains",
        "Contained in",
        "cgh outline {path}",
        nodes,
        sorted(set(edges)),
        truncated=max(0, len(defines) - len(kept)),
    )
