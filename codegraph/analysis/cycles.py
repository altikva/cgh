# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Import cycles in the File->File IMPORTS graph. tarjan_scc
#              finds strongly connected components iteratively (no
#              recursion limit on big graphs); find_import_cycles keeps the
#              ones that are real cycles. Shared by the import_cycles MCP
#              tool and the architecture report.

from __future__ import annotations

from typing import Any


def tarjan_scc(adj: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan strongly-connected components, iterative to avoid recursion
    limits on large import graphs. Returns a list of components (each a list
    of node keys)."""
    index_counter = [0]
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []

    for start in list(adj.keys()):
        if start in index:
            continue
        # Iterative DFS. work stack holds (node, neighbor_iterator_position).
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = lowlink[node] = index_counter[0]
                index_counter[0] += 1
                stack.append(node)
                on_stack[node] = True
            neighbors = adj.get(node, [])
            if pi < len(neighbors):
                work[-1] = (node, pi + 1)
                nxt = neighbors[pi]
                if nxt not in index:
                    work.append((nxt, 0))
                elif on_stack.get(nxt):
                    lowlink[node] = min(lowlink[node], index[nxt])
            else:
                if lowlink[node] == index[node]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        comp.append(w)
                        if w == node:
                            break
                    result.append(comp)
                work.pop()
                if work:
                    parent_node = work[-1][0]
                    lowlink[parent_node] = min(lowlink[parent_node], lowlink[node])
    return result


def find_import_cycles(conn: Any) -> list[list[str]]:
    """Every import cycle of the graph behind ``conn``: components of more
    than one file, or a file importing itself. Each cycle is sorted."""
    adj: dict[str, list[str]] = {}
    for row in conn.find_neighbors("IMPORTS", return_src=["path"], return_dst=["path"]):
        src = row["src_path"]
        dst = row["dst_path"]
        adj.setdefault(src, []).append(dst)
        adj.setdefault(dst, [])
    cycles: list[list[str]] = []
    for comp in tarjan_scc(adj):
        if len(comp) > 1:
            cycles.append(sorted(comp))
        elif len(comp) == 1 and comp[0] in adj.get(comp[0], []):
            cycles.append([comp[0]])
    return cycles
