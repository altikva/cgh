# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP query tools: symbol_lookup, callers, callees, imports_of,
#              indexed_files, search_symbols, subgraph.

from __future__ import annotations

import json
import os

# Forward call-chain traversal bounds for find_callees(max_depth>1): the
# hop ceiling, the per-node edge fan-out, and the total callees returned.
# Mirror the reverse-reach caps impact_of already uses.
_CALLEE_DEPTH_CAP = 5
_CALLEE_FANOUT_CAP = 500
_CALLEE_TOTAL_CAP = 300


def register(mcp) -> None:
    """Register query tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import (
        child_fts_symbol_lookup,
        child_fts_symbol_search,
        federate_flat,
    )
    from codegraph.server import _get_conn, _logged_tool

    def _federate(query_fn):
        """Parent + federated children fan-out, flattened. Returns
        (results_with_scope, warnings). See federation.federate_flat."""
        return federate_flat(_get_conn, _srv._root, query_fn)

    def _retry_children_via_fts(results, warnings, retry, to_row):
        """Re-answer children whose graph DB was unreachable from their FTS.

        ``retry(scopes)`` runs the FTS query for the locked scopes and
        ``to_row(hit, scope)`` shapes each hit like the tool's own rows.
        Returns the results with the recovered rows appended, and the warnings
        trimmed to the scopes that no backend could answer. The parent's own
        warning, if any, is left untouched.
        """
        locked = {
            w["scope"] for w in warnings if w.get("scope") and w["scope"] != "parent"
        }
        if not locked or _srv._root is None:
            return results, warnings
        buckets, failures = retry(locked)
        for scope, hits in buckets:
            results.extend(to_row(hit, scope) for hit in hits)
        kept = [w for w in warnings if w.get("scope") not in locked]
        kept.extend({"scope": scope, "error": error} for scope, error in failures)
        return results, kept

    @mcp.tool()
    @_logged_tool
    def pattern_search(
        pattern: str,
        glob: str = "",
        max_results: int = 50,
        regex: bool = True,
        case_sensitive: bool = False,
    ) -> str:
        """
        Where does this exact string or regex occur?
        Use INSTEAD of Grep, git grep, rg or grep -r. Returns {file, line,
        text} hits, then Read only those lines. Respects .gitignore, also
        covers extra_dirs, capped at max_results. For a definition use
        symbol_lookup; for a concept in words, fts_search.

        Args:
          pattern:        regex by default, literal when regex=False
          glob:           optional shell glob, e.g. "*.py", "api/handlers/*"
          max_results:    hard cap (default 50)
          regex:          treat pattern as regex (default True)
          case_sensitive: default False

        Example: pattern_search(r"@router\\.(get|post)", glob="*.py")
                 → list of route declarations with line numbers.

        Federated: also scans federated subrepo trees. Each hit is tagged
        with `scope` (parent / <subrepo-name>).
        """
        from codegraph.analysis.federation import resolve_children
        from codegraph.analysis.pattern import pattern_search as _search

        all_hits: list[dict] = []
        backend = ""
        # Parent scope
        hits, backend = _search(
            _srv._root,
            pattern=pattern,
            glob=glob,
            max_results=max_results,
            regex=regex,
            case_sensitive=case_sensitive,
        )
        for h in hits:
            all_hits.append(
                {"scope": "parent", "file": h.file, "line": h.line, "text": h.text}
            )

        # Each federated subrepo. Resolve children once and reuse for the cap
        # below (this used to read + parse config.toml twice per query).
        children = resolve_children(_srv._root) if _srv._root else []
        for child in children:
            try:
                child_hits, _ = _search(
                    child,
                    pattern=pattern,
                    glob=glob,
                    max_results=max_results,
                    regex=regex,
                    case_sensitive=case_sensitive,
                )
            except Exception:
                continue
            for h in child_hits:
                all_hits.append(
                    {
                        "scope": child.name,
                        "file": h.file,
                        "line": h.line,
                        "text": h.text,
                    }
                )

        # Apply max_results across the whole federation as a soft cap
        all_hits = all_hits[: max_results * (1 + len(children))]

        return json.dumps(
            {
                "pattern": pattern,
                "glob": glob or None,
                "backend": backend,
                "total": len(all_hits),
                "hits": all_hits,
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def symbol_lookup(name: str, role: str = "", layer: str = "") -> str:
        """
        Where is X defined?
        Use when you know the name of a function, class, TF resource or doc
        section. Returns file, line range, type and docstring head per
        definition; role / layer narrow it. Half-known name: search_symbols.
        Code described in words: fts_search. Literal text: pattern_search.
        """

        def query(conn):
            out = []
            for row in conn.find_nodes(
                "Function",
                where={"name": name},
                return_fields=[
                    "name",
                    "file_path",
                    "start_line",
                    "end_line",
                    "docstring",
                ],
            ):
                out.append(
                    {
                        "kind": "function",
                        "name": row["name"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                    }
                )
            for row in conn.find_nodes(
                "Class",
                where={"name": name},
                return_fields=[
                    "name",
                    "file_path",
                    "start_line",
                    "end_line",
                    "docstring",
                ],
            ):
                out.append(
                    {
                        "kind": "class",
                        "name": row["name"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                    }
                )
            for row in conn.find_nodes(
                "TFResource",
                where={"name": name},
                return_fields=["name", "file_path", "type", "start_line", "end_line"],
            ):
                out.append(
                    {
                        "kind": "tf_resource",
                        "name": row["name"],
                        "type": row["type"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                    }
                )
            # TFVar (terraform variable/output) has no end_line column, so it
            # gets its own block anchored on start_line.
            for row in conn.find_nodes(
                "TFVar",
                where={"name": name},
                return_fields=["name", "file_path", "kind", "start_line"],
            ):
                out.append(
                    {
                        "kind": "tf_var",
                        "name": row["name"],
                        "type": row["kind"],
                        "file": row["file_path"],
                        "lines": str(row["start_line"]),
                    }
                )
            for row in conn.find_nodes(
                "MdSection",
                contains={"title": name},
                return_fields=[
                    "title",
                    "file_path",
                    "start_line",
                    "end_line",
                    "body_preview",
                    "anchor",
                ],
            ):
                out.append(
                    {
                        "kind": "md_section",
                        "name": row["title"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["body_preview"] or "")[:120],
                        "anchor": row["anchor"],
                    }
                )
            if role or layer:
                cache: dict[str, tuple[str, str]] = {}
                kept = []
                for hit in out:
                    fp = hit.get("file")
                    if not fp:
                        continue
                    if fp not in cache:
                        cache[fp] = _file_role_layer(conn, fp)
                    r, lyr = cache[fp]
                    if role and r != role:
                        continue
                    if layer and lyr != layer:
                        continue
                    kept.append(hit)
                return kept
            return out

        results, warnings = _federate(query)
        if warnings and not (role or layer):
            # Same fallback as search_symbols: a child whose owner holds the
            # graph write lock still resolves the name through its FTS index.
            results, warnings = _retry_children_via_fts(
                results,
                warnings,
                lambda scopes: child_fts_symbol_lookup(_srv._root, name, scopes),
                lambda hit, scope: {
                    "kind": hit.kind,
                    "name": hit.name,
                    "file": hit.file_path,
                    "lines": f"{hit.start_line}-{hit.end_line}",
                    "doc": hit.docstring[:120],
                    "scope": scope,
                },
            )
        if not results:
            payload = {"found": False, "name": name}
            if warnings:
                payload["partial"] = True
                payload["warnings"] = warnings
            return json.dumps(payload)
        out = {"found": True, "name": name, "definitions": results}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_callers(fn_name: str) -> str:
        """
        Who calls X?
        Returns each function that calls `fn_name`. Calls match by name:
        when several definitions share it, each caller lists the matched
        files in `targets`. Transitive callers: impact_of. What X calls:
        find_callees. Federated, scope-tagged; cross-repo calls are not
        inferred.
        """

        from codegraph.analysis.callers import callers_of

        callers, warnings = _federate(lambda conn: callers_of(conn, fn_name))
        out = {"fn": fn_name, "callers": callers}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_callees(fn_name: str, max_depth: int = 1) -> str:
        """
        What does X call?
        Returns the functions `fn_name` calls; max_depth > 1 follows the
        chain forward in one call (each callee has `depth`, 1 = direct).
        Name-matched, so a callee may be a same-named function elsewhere;
        `truncated` means a cap was hit. Who calls X: find_callers.
        Federated; cross-repo calls are not inferred.
        """
        depth_cap = max(1, min(int(max_depth), _CALLEE_DEPTH_CAP))
        state = {"truncated": False}

        def query(conn):
            seen: set[str] = {fn_name}  # names already expanded (cycle guard)
            frontier = [fn_name]
            out: list[dict] = []
            depth = 0
            while frontier and depth < depth_cap:
                depth += 1
                nxt: list[str] = []
                for name in frontier:
                    rows = conn.find_neighbors(
                        "CALLS",
                        src_where={"name": name},
                        return_dst=["name", "file_path", "start_line"],
                        limit=_CALLEE_FANOUT_CAP,
                    )
                    if len(rows) >= _CALLEE_FANOUT_CAP:
                        state["truncated"] = True
                    for row in rows:
                        callee = row["dst_name"]
                        out.append(
                            {
                                "callee": callee,
                                "file": row["dst_file_path"],
                                "line": row["dst_start_line"],
                                "depth": depth,
                            }
                        )
                        if callee not in seen:
                            seen.add(callee)
                            nxt.append(callee)
                    if len(out) >= _CALLEE_TOTAL_CAP:
                        state["truncated"] = True
                        return out
                frontier = nxt
            return out

        callees, warnings = _federate(query)
        out = {"fn": fn_name, "max_depth": depth_cap, "callees": callees}
        if state["truncated"]:
            out["truncated"] = True
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def imports_of(file_path: str) -> str:
        """
        What does this file import?
        Returns the modules `file_path` imports (relative to the repo root or
        absolute; the file may live in any subrepo). Who imports it:
        file_summary, or impact_of for the transitive importers.
        """
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        def query(conn):
            return [
                {"module": row["dst_path"], "symbol": row.get("edge_symbol", "")}
                for row in conn.find_neighbors(
                    "IMPORTS",
                    src_key=file_path,
                    return_dst=["path"],
                    return_edge=["symbol"],
                )
            ]

        imports, warnings = _federate(query)
        out = {"file": file_path, "imports": imports}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def indexed_files(pattern: str = "", limit: int = 200, path: str = "") -> str:
        """
        Is this file indexed, or which files are?
        Without `path`: indexed paths containing `pattern`, sorted, as
        `files` (first `limit`) plus `total`. With `path`: `indexed` says
        whether that exact file is in the index. Parent scope only; sees
        files that define no symbol, unlike fts_search.
        """
        conn = _get_conn()
        if path:
            if not os.path.isabs(path) and _srv._root:
                path = str(_srv._root / path)
            hit = conn.query_node_field("File", "path", path, "path")
            return json.dumps({"path": path, "indexed": hit is not None})
        found = conn.find_nodes(
            "File",
            contains={"path": pattern} if pattern else None,
            return_fields=["path"],
            order_by=["path"],
        )
        paths = [r["path"] for r in found]
        return json.dumps(
            {"pattern": pattern, "total": len(paths), "files": paths[: max(limit, 0)]},
            indent=2,
        )

    def _file_role_layer(conn, file_path: str) -> tuple[str, str]:
        """Return (role, layer) for a File node, ('', '') when unknown."""
        nodes = conn.find_nodes(
            "File",
            where={"path": file_path},
            return_fields=["role", "layer"],
            limit=1,
        )
        if not nodes:
            return "", ""
        return (nodes[0].get("role") or "", nodes[0].get("layer") or "")

    @mcp.tool()
    @_logged_tool
    def search_symbols(
        query: str,
        limit: int = 20,
        role: str = "",
        layer: str = "",
        kinds: str = "",
        name_only: bool = False,
    ) -> str:
        """
        Which symbols have a name like X?
        Substring match on symbol names, for a half-known name. Filters:
        role, layer, kinds (function, class, tf_resource, tf_var,
        md_section), name_only. Federated: `limit` is per scope. Exact name:
        symbol_lookup. Code described in words: fts_search.
        """
        wanted = {k.strip() for k in kinds.split(",") if k.strip()}

        def _want(kind: str) -> bool:
            return not wanted or kind in wanted

        def run(conn):
            out = []
            for label, kind in [("Function", "function"), ("Class", "class")]:
                if not _want(kind):
                    continue
                for row in conn.find_nodes(
                    label,
                    contains={"name": query},
                    return_fields=["name", "file_path", "start_line"],
                    limit=limit,
                ):
                    out.append(
                        {
                            "kind": kind,
                            "name": row["name"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                        }
                    )
            if _want("tf_resource"):
                match = {"name": query} if name_only else {"name": query, "type": query}
                for row in conn.find_nodes(
                    "TFResource",
                    contains=match,
                    return_fields=["name", "type", "file_path", "start_line"],
                    limit=limit,
                ):
                    out.append(
                        {
                            "kind": "tf_resource",
                            "name": row["name"],
                            "type": row["type"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                        }
                    )
            if _want("tf_var"):
                match = {"name": query} if name_only else {"name": query, "kind": query}
                for row in conn.find_nodes(
                    "TFVar",
                    contains=match,
                    return_fields=["name", "kind", "file_path", "start_line"],
                    limit=limit,
                ):
                    out.append(
                        {
                            "kind": "tf_var",
                            "name": row["name"],
                            "type": row["kind"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                        }
                    )
            if _want("md_section"):
                match = (
                    {"title": query}
                    if name_only
                    else {"title": query, "body_preview": query}
                )
                for row in conn.find_nodes(
                    "MdSection",
                    contains=match,
                    return_fields=[
                        "title",
                        "file_path",
                        "start_line",
                        "level",
                        "anchor",
                    ],
                    limit=limit,
                ):
                    out.append(
                        {
                            "kind": "md_section",
                            "name": row["title"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                            "level": row["level"],
                            "anchor": row["anchor"],
                        }
                    )
            if role or layer:
                # Filter each hit by its File node's role / layer. Cache
                # per-file lookups so repeated hits in the same file cost one
                # query. Markdown / TF hits without a File node drop out.
                cache: dict[str, tuple[str, str]] = {}
                kept = []
                for hit in out:
                    fp = hit.get("file")
                    if not fp:
                        continue
                    if fp not in cache:
                        cache[fp] = _file_role_layer(conn, fp)
                    r, lyr = cache[fp]
                    if role and r != role:
                        continue
                    if layer and lyr != layer:
                        continue
                    kept.append(hit)
                return kept
            return out

        results, warnings = _federate(run)
        if warnings and not (role or layer):
            # A child whose own owner holds the graph write lock cannot be
            # opened read-only from here. Retry it over its FTS index, which
            # takes concurrent readers, so it stays in the results instead of
            # coming back as a partial scope. The role / layer filters need
            # the child's File nodes, so a filtered search keeps the warning.
            results, warnings = _retry_children_via_fts(
                results,
                warnings,
                lambda scopes: child_fts_symbol_search(
                    _srv._root, query, limit, scopes
                ),
                lambda hit, scope: {
                    "kind": hit.kind,
                    "name": hit.name,
                    "file": hit.file_path,
                    "line": hit.start_line,
                    "scope": scope,
                },
            )
        payload = {"query": query, "results": results}
        if kinds:
            payload["kinds"] = kinds
        if name_only:
            payload["name_only"] = True
        if role:
            payload["role"] = role
        if layer:
            payload["layer"] = layer
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)

    @mcp.tool()
    @_logged_tool
    def subgraph(file_path: str, depth: int = 1) -> str:
        """
        Which files does this file import, and which import it?
        depth=1 returns `depends_on` and `depended_by`; depth > 1 returns
        every file reachable through its imports. Federated: each scope is
        walked on its own, results concatenated. Everything that depends on
        it, transitively: impact_of.
        """
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        if depth == 1:

            def run_deps(conn):
                return [
                    {
                        "kind": "depends_on",
                        "file": row["dst_path"],
                        "lang": row["dst_lang"],
                    }
                    for row in conn.find_neighbors(
                        "IMPORTS", src_key=file_path, return_dst=["path", "lang"]
                    )
                ]

            def run_rdeps(conn):
                return [
                    {
                        "kind": "depended_by",
                        "file": row["src_path"],
                        "lang": row["src_lang"],
                    }
                    for row in conn.find_neighbors(
                        "IMPORTS", dst_key=file_path, return_src=["path", "lang"]
                    )
                ]

            deps_all, w1 = _federate(run_deps)
            rdeps_all, w2 = _federate(run_rdeps)
            depends_on = [{k: v for k, v in d.items() if k != "kind"} for d in deps_all]
            depended_by = [
                {k: v for k, v in d.items() if k != "kind"} for d in rdeps_all
            ]
            payload = {
                "file": file_path,
                "depth": depth,
                "depends_on": depends_on,
                "depended_by": depended_by,
            }
            warnings = w1 + w2
            if warnings:
                payload["partial"] = True
                payload["warnings"] = warnings
            return json.dumps(payload, indent=2)

        # depth >= 2, recursive traversal via the GraphDB helper
        def run_reach(conn):
            return [
                {"file": row["path"], "lang": row["lang"]}
                for row in conn.reach_via_edge(
                    "IMPORTS",
                    file_path,
                    max_depth=int(depth),
                    return_fields=["path", "lang"],
                )
            ]

        deps, warnings = _federate(run_reach)
        payload = {"file": file_path, "depth": depth, "reachable": deps}
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)
