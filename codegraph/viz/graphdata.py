# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Whole-graph payload for the interactive browser view: every
#              indexed file with its language, role, layer and symbol counts,
#              the file-to-file import edges, and the most connected functions
#              with the calls between them. Mermaid views cap at a few dozen
#              nodes because the diagram becomes unreadable; this payload is
#              meant for the force-directed canvas, which handles the whole
#              repo, so only the symbol side is capped (by call degree).

from __future__ import annotations

import os
from collections import Counter
from typing import Any

# Functions carried into the symbol view, ranked by call degree. The file
# view is never capped: a repo's files are the point of the overview.
DEFAULT_MAX_SYMBOLS = 400


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


def build_graph_payload(
    conn: Any, root: str, max_symbols: int = DEFAULT_MAX_SYMBOLS
) -> dict:
    """Build the JSON payload the interactive graph view renders.

    Keys are short because the payload is inlined in the generated HTML and
    a large repo ships tens of thousands of rows:
      files:   p path, g directory group, l language, r role, y layer,
               f function count, c class count
      imports: [source index, target index] into files
      symbols: n name, p file path, g directory group, s start line
      calls:   [source index, target index] into symbols
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

    imports: set[tuple[int, int]] = set()
    for edge in conn.find_neighbors(
        "IMPORTS", return_src=["path"], return_dst=["path"]
    ):
        src = file_at.get(edge.get("src_path"))
        dst = file_at.get(edge.get("dst_path"))
        if src is not None and dst is not None and src != dst:
            imports.add((src, dst))

    file_rows = [
        {
            "p": _rel(root, f["path"]),
            "g": _group(_rel(root, f["path"])),
            "l": f.get("lang") or "",
            "r": f.get("role") or "",
            "y": f.get("layer") or "",
            "f": fn_per_file.get(f["path"], 0),
            "c": cls_per_file.get(f["path"], 0),
        }
        for f in files
    ]

    by_id = {r["id"]: r for r in functions if r.get("id")}
    calls_raw = [
        (e.get("src_id"), e.get("dst_id"))
        for e in conn.find_neighbors("CALLS", return_src=["id"], return_dst=["id"])
    ]
    degree: Counter = Counter()
    for src, dst in calls_raw:
        if src in by_id and dst in by_id and src != dst:
            degree[src] += 1
            degree[dst] += 1
    kept = [sym_id for sym_id, _ in degree.most_common(max(0, max_symbols))]
    sym_at = {sym_id: i for i, sym_id in enumerate(kept)}
    symbol_rows = [
        {
            "n": by_id[sym_id].get("name") or "",
            "p": _rel(root, by_id[sym_id].get("file_path") or ""),
            "g": _group(_rel(root, by_id[sym_id].get("file_path") or "")),
            "s": by_id[sym_id].get("start_line") or 0,
        }
        for sym_id in kept
    ]
    call_rows = sorted(
        {
            (sym_at[src], sym_at[dst])
            for src, dst in calls_raw
            if src in sym_at and dst in sym_at and src != dst
        }
    )

    return {
        "repo": os.path.basename(root) or root,
        "files": file_rows,
        "imports": sorted(imports),
        "symbols": symbol_rows,
        "calls": [list(pair) for pair in call_rows],
        "totals": {
            "files": len(file_rows),
            "functions": len(functions),
            "classes": len(classes),
            "symbols_shown": len(symbol_rows),
        },
    }
