# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Layer-diagram helpers on the GraphDB protocol for code graph visualization.
#              Consolidated from server.py viz helpers and visualize.py.

from __future__ import annotations

from codegraph.analysis.roles import LAYER_ORDER as _LAYER_ORDER
from codegraph.core.utils import safe_id as _safe_id

# ---------------------------------------------------------------------------
# Mermaid generators (from visualize.py, used by CLI cmd_graph)
# ---------------------------------------------------------------------------


def _layer_edge_counts(conn) -> dict[tuple[str, str], int]:
    """Aggregate IMPORTS edges into layer->layer counts using only the
    GraphDB protocol (backend-neutral, no raw SQL). Looks up each File
    node's stored `layer`; files without one fall back to "other".

    Returns {(src_layer, dst_layer): edge_count}, self-loops included so a
    layer that imports within itself stays visible.
    """
    # File path -> layer, one pass. Files missing a layer map to "other".
    layer_of: dict[str, str] = {}
    for f in conn.find_nodes("File", return_fields=["path", "layer"]):
        layer_of[f["path"]] = (f.get("layer") or "other") or "other"

    counts: dict[tuple[str, str], int] = {}
    for r in conn.find_neighbors("IMPORTS", return_src=["path"], return_dst=["path"]):
        src = r.get("src_path")
        dst = r.get("dst_path")
        if not src or not dst:
            continue
        key = (layer_of.get(src, "other"), layer_of.get(dst, "other"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _layer_sort_key(layer: str) -> tuple[int, str]:
    """Order layers by LAYER_ORDER, unknown layers sort last alphabetically."""
    try:
        return (_LAYER_ORDER.index(layer), layer)
    except ValueError:
        return (len(_LAYER_ORDER), layer)


def mermaid_layers(conn, root: str = "", max_nodes: int = 40) -> str:
    """Layer-dependency diagram: one node per architectural layer, one edge
    per layer->layer IMPORTS relationship labelled with its edge count.

    Built from File nodes' `layer` field (set by analysis.roles at index
    time) plus the IMPORTS graph. Layers are ordered by roles.LAYER_ORDER so
    presentation sits above application, above domain, above infra. Useful to
    spot inverted dependencies such as a domain file importing presentation.
    """
    counts = _layer_edge_counts(conn)
    if not counts:
        return "graph TD\n  NO_LAYERS[No layer import edges found]"

    layers = {sl for sl, _dl in counts} | {dl for _sl, dl in counts}
    ordered = sorted(layers, key=_layer_sort_key)

    lines = ["graph TD"]
    for layer in ordered:
        lid = _safe_id(layer)
        lines.append(f'  {lid}["{layer}"]:::layer')

    # Deterministic edge order: by source then destination layer.
    for sl, dl in sorted(
        counts, key=lambda e: (_layer_sort_key(e[0]), _layer_sort_key(e[1]))
    ):
        n = counts[(sl, dl)]
        sid, did = _safe_id(sl), _safe_id(dl)
        lines.append(f"  {sid} -->|{n}| {did}")

    lines.append("  classDef layer fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px")
    return "\n".join(lines)
