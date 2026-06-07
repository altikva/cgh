# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read-only graph-insight MCP tools built over the GraphDB
#              protocol: file_summary (one-shot file orientation),
#              impact_of (reverse blast radius over CALLS / IMPORTS),
#              path_between (shortest path over an edge type), and
#              import_cycles (SCC detection on the IMPORTS graph). All are
#              federated across subrepos and return JSON strings.

from __future__ import annotations

import json
import os

# Hard caps so a pathological graph never blows up the JSON response.
_SYMBOL_CAP = 200
_IMPACT_CAP = 300
_FANOUT_CAP = 500
_PATH_VISIT_CAP = 5000

# Reverse CALLS reach over-counts because CALLS edges are name-matched
# best-effort (same caveat find_dead_code carries). Keep the wording in
# one place so the note stays consistent.
_CALLS_NOTE = (
    "CALLS edges are name-matched best-effort, so reverse reach may "
    "over-count: a caller listed here may resolve to a same-named symbol "
    "in another file. Treat as a candidate set, not ground truth."
)


def register(mcp) -> None:
    """Register graph-insight tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import federate_flat, for_each_child_graphdb
    from codegraph.server import _get_conn, _logged_tool

    def _federate(query_fn):
        """Parent + federated children fan-out, flattened. Returns
        (results_with_scope, warnings). See federation.federate_flat."""
        return federate_flat(_get_conn, _srv._root, query_fn)

    def _abs(path: str) -> str:
        """Resolve a repo-relative path against the parent root."""
        if not os.path.isabs(path) and _srv._root:
            return str(_srv._root / path)
        return path

    def _looks_like_path(arg: str) -> bool:
        """Heuristic: does this argument name a file rather than a symbol?"""
        if "/" in arg or "\\" in arg:
            return True
        _, ext = os.path.splitext(arg)
        return ext in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".vue",
            ".go",
            ".rs",
            ".java",
            ".tf",
            ".md",
        }

    @mcp.tool()
    @_logged_tool
    def file_summary(file_path: str) -> str:
        """
        One-shot orientation for a single file. Returns its role / layer /
        lang / module_doc, the functions and classes it defines (name, line
        range, docstring head), the modules it imports, and the files that
        import it. Use this BEFORE reading a file to decide which line
        ranges actually matter.

        Args:
          file_path: repo-relative or absolute path to the File node.

        Federated: the file may live in the parent or in any subrepo, so we
        query all scopes and aggregate. Each symbol / import row carries a
        `scope` tag. Symbols are capped at 200 with a truncation note.
        """
        target = _abs(file_path)

        def query(conn):
            rows: list[dict] = []
            # File node metadata. There is at most one per scope.
            for f in conn.find_nodes(
                "File",
                where={"path": target},
                return_fields=["path", "lang", "role", "layer", "module_doc"],
            ):
                rows.append(
                    {
                        "_kind": "file",
                        "path": f["path"],
                        "lang": f.get("lang") or "",
                        "role": f.get("role") or "",
                        "layer": f.get("layer") or "",
                        "module_doc": (f.get("module_doc") or "")[:300],
                    }
                )
            for label, kind in [("Function", "function"), ("Class", "class")]:
                for s in conn.find_nodes(
                    label,
                    where={"file_path": target},
                    return_fields=["name", "start_line", "end_line", "docstring"],
                    order_by=["start_line"],
                    limit=_SYMBOL_CAP + 1,
                ):
                    rows.append(
                        {
                            "_kind": "symbol",
                            "symbol_kind": kind,
                            "name": s["name"],
                            "lines": f"{s['start_line']}-{s['end_line']}",
                            "doc": (s.get("docstring") or "")[:100],
                        }
                    )
            for imp in conn.find_neighbors(
                "IMPORTS", src_key=target, return_dst=["path"]
            ):
                rows.append({"_kind": "import", "module": imp["dst_path"]})
            for imp in conn.find_neighbors(
                "IMPORTS", dst_key=target, return_src=["path"]
            ):
                rows.append({"_kind": "imported_by", "file": imp["src_path"]})
            return rows

        results, warnings = _federate(query)

        meta = {"role": "", "layer": "", "lang": "", "module_doc": ""}
        functions: list[dict] = []
        classes: list[dict] = []
        imports: list[dict] = []
        imported_by: list[dict] = []
        found = False
        for row in results:
            kind = row.pop("_kind")
            scope = row.get("scope", "parent")
            if kind == "file":
                found = True
                for k in ("role", "layer", "lang", "module_doc"):
                    if not meta[k] and row.get(k):
                        meta[k] = row[k]
            elif kind == "symbol":
                bucket = functions if row["symbol_kind"] == "function" else classes
                bucket.append(
                    {
                        "name": row["name"],
                        "lines": row["lines"],
                        "doc": row["doc"],
                        "scope": scope,
                    }
                )
            elif kind == "import":
                imports.append({"module": row["module"], "scope": scope})
            elif kind == "imported_by":
                imported_by.append({"file": row["file"], "scope": scope})

        total_symbols = len(functions) + len(classes)
        truncated = total_symbols > _SYMBOL_CAP
        if truncated:
            # Trim functions first, then classes, to the global cap.
            keep_fn = min(len(functions), _SYMBOL_CAP)
            functions = functions[:keep_fn]
            classes = classes[: max(0, _SYMBOL_CAP - keep_fn)]

        payload: dict = {
            "file": target,
            "found": found,
            "role": meta["role"],
            "layer": meta["layer"],
            "lang": meta["lang"],
            "module_doc": meta["module_doc"],
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "imported_by": imported_by,
            "truncated": truncated,
        }
        if truncated:
            payload["note"] = f"symbols capped at {_SYMBOL_CAP}"
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)

    @mcp.tool()
    @_logged_tool
    def impact_of(symbol_or_file: str, max_depth: int = 3) -> str:
        """
        Reverse blast radius: what depends on a symbol or file. If the
        argument looks like a path (or matches a File node) we walk IMPORTS
        backward to find every file that transitively imports it. Otherwise
        we resolve it as a function name and walk CALLS backward to find
        every transitive caller.

        Args:
          symbol_or_file: a function name, or a repo-relative / absolute path.
          max_depth:      how many hops of reverse reach (default 3).

        Returns JSON with `direction` ("callers" or "importers"), the
        `impacted` set grouped by role / layer for files, any reaching
        endpoints, a `count`, and `truncated`.

        Federated per scope. CALLS reverse reach over-counts (see `note`):
        edges are name-matched, so a listed caller may belong to a
        same-named symbol elsewhere.
        """
        arg = symbol_or_file
        as_path = _looks_like_path(arg)

        def _resolve_is_file(conn) -> bool:
            hits = conn.find_nodes("File", where={"path": _abs(arg)}, limit=1)
            return bool(hits)

        # Decide direction once, against the parent conn, then reuse it for
        # every scope so the result set is homogeneous.
        if not as_path:
            try:
                as_path = _resolve_is_file(_get_conn())
            except Exception:
                as_path = False

        direction = "importers" if as_path else "callers"
        edge = "IMPORTS" if as_path else "CALLS"

        def reverse_bfs(conn, start_keys: list[str]) -> tuple[list[str], bool]:
            """Bounded reverse BFS: collect source keys reachable into the
            start keys within max_depth hops. Returns (keys, truncated)."""
            seen: set[str] = set(start_keys)
            frontier = list(start_keys)
            ordered: list[str] = []
            truncated = False
            depth = 0
            while frontier and depth < max(1, int(max_depth)):
                depth += 1
                next_frontier: list[str] = []
                for key in frontier:
                    if as_path:
                        rows = conn.find_neighbors(
                            edge, dst_key=key, return_src=["path"], limit=_FANOUT_CAP
                        )
                        srcs = [r["src_path"] for r in rows]
                    else:
                        rows = conn.find_neighbors(
                            edge, dst_key=key, return_src=["id"], limit=_FANOUT_CAP
                        )
                        srcs = [r["src_id"] for r in rows]
                    if len(rows) >= _FANOUT_CAP:
                        truncated = True
                    for s in srcs:
                        if s in seen:
                            continue
                        seen.add(s)
                        ordered.append(s)
                        next_frontier.append(s)
                        if len(ordered) >= _IMPACT_CAP:
                            return ordered, True
                frontier = next_frontier
            return ordered, truncated

        def query(conn):
            # Resolve the starting key(s) within this scope.
            if as_path:
                start_keys = [_abs(arg)]
            else:
                start_keys = [
                    r["id"]
                    for r in conn.find_nodes(
                        "Function", where={"name": arg}, return_fields=["id"]
                    )
                ]
            if not start_keys:
                return []
            keys, trunc = reverse_bfs(conn, start_keys)

            out: list[dict] = []
            if as_path:
                # Each impacted key is a file path; enrich with role / layer.
                for path in keys:
                    role = layer = lang = ""
                    fnodes = conn.find_nodes(
                        "File",
                        where={"path": path},
                        return_fields=["role", "layer", "lang"],
                        limit=1,
                    )
                    if fnodes:
                        role = fnodes[0].get("role") or ""
                        layer = fnodes[0].get("layer") or ""
                        lang = fnodes[0].get("lang") or ""
                    out.append(
                        {
                            "node": path,
                            "node_kind": "file",
                            "role": role,
                            "layer": layer,
                            "lang": lang,
                            "_trunc": trunc,
                        }
                    )
            else:
                # Each impacted key is a Function id "file::name".
                for fid in keys:
                    file_path = fid.rsplit("::", 1)[0] if "::" in fid else ""
                    role = layer = ""
                    if file_path:
                        fnodes = conn.find_nodes(
                            "File",
                            where={"path": file_path},
                            return_fields=["role", "layer"],
                            limit=1,
                        )
                        if fnodes:
                            role = fnodes[0].get("role") or ""
                            layer = fnodes[0].get("layer") or ""
                    out.append(
                        {
                            "node": fid,
                            "node_kind": "function",
                            "file": file_path,
                            "role": role,
                            "layer": layer,
                            "_trunc": trunc,
                        }
                    )
            return out

        results, warnings = _federate(query)

        impacted: list[dict] = []
        by_role: dict[str, int] = {}
        by_layer: dict[str, int] = {}
        endpoints: list[dict] = []
        truncated = len(results) > _IMPACT_CAP
        files_for_endpoints: set[tuple[str, str]] = set()
        for row in results:
            if row.pop("_trunc", False):
                truncated = True
            scope = row.get("scope", "parent")
            role = row.get("role") or ""
            layer = row.get("layer") or ""
            if role:
                by_role[role] = by_role.get(role, 0) + 1
            if layer:
                by_layer[layer] = by_layer.get(layer, 0) + 1
            impacted.append(row)
            fp = row.get("file") or (row["node"] if row["node_kind"] == "file" else "")
            if fp:
                files_for_endpoints.add((scope, fp))
            if role and ("router" in role.lower() or "endpoint" in role.lower()):
                endpoints.append(
                    {"file": fp or row["node"], "role": role, "scope": scope}
                )

        if len(impacted) > _IMPACT_CAP:
            impacted = impacted[:_IMPACT_CAP]
            truncated = True

        # Endpoints declared in any impacted file (DEFINES_ENDPOINT).
        def endpoint_query(conn):
            rows: list[dict] = []
            for _scope, fp in files_for_endpoints:
                for e in conn.find_neighbors(
                    "DEFINES_ENDPOINT",
                    src_key=fp,
                    return_dst=["method", "path"],
                ):
                    rows.append(
                        {
                            "file": fp,
                            "method": e.get("dst_method", ""),
                            "path": e.get("dst_path", ""),
                        }
                    )
            return rows

        if files_for_endpoints:
            ep_rows, ep_warnings = _federate(endpoint_query)
            warnings = warnings + ep_warnings
            seen_ep = {(e["file"], e.get("path", "")) for e in endpoints}
            for e in ep_rows:
                key = (e["file"], e.get("path", ""))
                if key in seen_ep:
                    continue
                seen_ep.add(key)
                endpoints.append(e)

        payload: dict = {
            "target": arg,
            "direction": direction,
            "depth": int(max_depth),
            "impacted": impacted,
            "count": len(impacted),
            "by_role": by_role,
            "by_layer": by_layer,
            "endpoints": endpoints,
            "truncated": truncated,
        }
        if not as_path:
            payload["note"] = _CALLS_NOTE
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)

    @mcp.tool()
    @_logged_tool
    def path_between(src: str, dst: str, edge: str = "CALLS") -> str:
        """
        Shortest path between two symbols or files over an edge type. With
        edge="CALLS" (default) src / dst are function names; with
        edge="IMPORTS" they are file paths. Runs a forward BFS from src and
        reconstructs the first path that reaches dst.

        Args:
          src:  start function name (CALLS) or file path (IMPORTS).
          dst:  end function name (CALLS) or file path (IMPORTS).
          edge: "CALLS" or "IMPORTS" (default "CALLS").

        Returns JSON `{src, dst, edge, path: [...], length}` or
        `{found: false}`. Per scope: a path is reported from the first scope
        that contains one, so it never crosses repo boundaries.
        """
        edge = (edge or "CALLS").upper()
        if edge not in {"CALLS", "IMPORTS"}:
            return json.dumps(
                {"found": False, "error": f"unsupported edge type: {edge}"}
            )
        is_calls = edge == "CALLS"

        def query(conn):
            # Resolve start / end keys for this scope.
            if is_calls:
                start_ids = [
                    r["id"]
                    for r in conn.find_nodes(
                        "Function", where={"name": src}, return_fields=["id"]
                    )
                ]
                dst_ids = {
                    r["id"]
                    for r in conn.find_nodes(
                        "Function", where={"name": dst}, return_fields=["id"]
                    )
                }
                ret = ["id"]
                ret_key = "dst_id"
            else:
                start_ids = [_abs(src)]
                dst_ids = {_abs(dst)}
                ret = ["path"]
                ret_key = "dst_path"
            if not start_ids or not dst_ids:
                return None

            # Forward BFS with parent pointers for path reconstruction.
            visited: set[str] = set(start_ids)
            parent: dict[str, str | None] = {k: None for k in start_ids}
            frontier = list(start_ids)
            hit: str | None = None
            for s in start_ids:
                if s in dst_ids:
                    hit = s
                    break
            while frontier and hit is None and len(visited) < _PATH_VISIT_CAP:
                next_frontier: list[str] = []
                for cur in frontier:
                    rows = conn.find_neighbors(edge, src_key=cur, return_dst=ret)
                    for r in rows:
                        nxt = r[ret_key]
                        if nxt in visited:
                            continue
                        visited.add(nxt)
                        parent[nxt] = cur
                        if nxt in dst_ids:
                            hit = nxt
                            break
                        next_frontier.append(nxt)
                    if hit is not None:
                        break
                frontier = next_frontier
            if hit is None:
                return None
            # Reconstruct.
            chain: list[str] = []
            node: str | None = hit
            while node is not None:
                chain.append(node)
                node = parent.get(node)
            chain.reverse()
            return chain

        results = None
        warnings: list[dict] = []
        # We need per-scope payloads (a path lives in one scope), so use the
        # scoped variant directly rather than the flat helper.
        try:
            parent_chain = query(_get_conn())
        except Exception as exc:
            parent_chain = None
            warnings.append(
                {"scope": "parent", "error": f"{type(exc).__name__}: {exc}"}
            )
        if parent_chain:
            results = ("parent", parent_chain)
        if results is None and _srv._root is not None:
            for s in for_each_child_graphdb(_srv._root, lambda c, _r: query(c)):
                if s.error:
                    warnings.append({"scope": s.scope, "error": s.error})
                    continue
                if s.payload:
                    results = (s.scope, s.payload)
                    break

        if results is None:
            payload: dict = {"src": src, "dst": dst, "edge": edge, "found": False}
            if warnings:
                payload["partial"] = True
                payload["warnings"] = warnings
            return json.dumps(payload, indent=2)

        scope, chain = results
        payload = {
            "src": src,
            "dst": dst,
            "edge": edge,
            "found": True,
            "scope": scope,
            "path": chain,
            "length": len(chain) - 1,
        }
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)

    @mcp.tool()
    @_logged_tool
    def import_cycles(limit: int = 50) -> str:
        """
        Detect import cycles in the File->File IMPORTS graph. Builds the
        adjacency from every IMPORTS edge and reports each strongly-connected
        component of size > 1 (a cycle). Runs per scope: cycles never cross
        repo boundaries, so each component lives in one scope.

        Args:
          limit: cap on the number of cycles returned (default 50).

        Returns JSON `{cycles: [[file, file, ...], ...], count, truncated}`,
        each cycle tagged inline with its scope via the file paths it holds.
        """

        def query(conn):
            adj: dict[str, list[str]] = {}
            for row in conn.find_neighbors(
                "IMPORTS", return_src=["path"], return_dst=["path"]
            ):
                src = row["src_path"]
                dst = row["dst_path"]
                adj.setdefault(src, []).append(dst)
                adj.setdefault(dst, [])
            comps = _tarjan_scc(adj)
            # Only components that are a real cycle: size > 1, or a self-loop.
            cycles: list[dict] = []
            for comp in comps:
                if len(comp) > 1:
                    cycles.append({"cycle": sorted(comp)})
                elif len(comp) == 1:
                    node = comp[0]
                    if node in adj.get(node, []):
                        cycles.append({"cycle": [node]})
            return cycles

        results, warnings = _federate(query)
        cycles = [r["cycle"] for r in results]
        truncated = len(cycles) > limit
        cycles = cycles[:limit]
        payload: dict = {
            "cycles": cycles,
            "count": len(cycles),
            "truncated": truncated,
        }
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)


def _tarjan_scc(adj: dict[str, list[str]]) -> list[list[str]]:
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
