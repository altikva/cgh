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
    from codegraph.analysis.terraform import lookup as tf_lookup
    from codegraph.analysis.terraform import search as tf_search
    from codegraph.analysis.terraform import used_by as tf_used_by
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
        Regex / substring pattern search across the indexed repo. Use this
        INSTEAD of the Grep tool for any "find all occurrences of X"
        question, returns structured {file, line, text} hits so you can
        then Read the exact line ranges that matter (never whole files).

        Respects .gitignore (via ripgrep / git-grep). Automatically scans
        extra_dirs configured on this project. Caps output at max_results.

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
        Find where a symbol (function, class, TF resource) is defined.
        Returns file path, line range, type, and docstring snippet, plus
        a `scope` tag (parent / <subrepo-name>) when federation is on.
        Use this instead of grepping files.

        Optional `role` / `layer` filters keep only definitions whose File
        node carries that exact role / layer (empty = no filter).

        Each definition carries its `name` (the section title for a
        markdown section, which matches on a title substring).

        Terraform blocks are found by address too (var.region,
        google_x.y, module.m, local.l, data.t.n, output.o).
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
            # Terraform blocks, by address (var.region, google_x.y) or by
            # bare block name.
            for hit in tf_lookup(conn, name):
                out.append(
                    {
                        "kind": hit["kind"],
                        "name": hit["name"],
                        "type": hit["type"],
                        "file": hit["file"],
                        "lines": f"{hit['start_line']}-{hit['end_line']}",
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
        Find all functions that call `fn_name`. Federated across subrepos,
        each result tagged with `scope`. Note: cross-repo CALLS edges are
        not inferred (each subrepo's graph is canonical for its own code).
        Calls are matched by name: when several functions share `fn_name`
        (a method and its test double), each caller lists the matched
        definitions' files in `targets`.

        A `Class.method` name also lists the callers of the interface
        methods it implements (a base class, an ABC, a Protocol the class
        conforms to), since a call through the interface links to the
        interface's method; those rows carry `via` ("KmsClient.encrypt").

        For a Terraform address (var.region, google_x.y, module.m) it
        returns each block that uses it.
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
        Find the functions `fn_name` calls. With `max_depth=1` (default)
        that is the direct callees. With `max_depth>1` it walks the CALLS
        edges forward transitively, so one call returns the ordered call
        chain (each callee carries its `depth`, 1 = direct) instead of
        forcing a separate lookup per hop. Use it to trace a flow in a
        single call.

        Federated across subrepos; cross-repo CALLS edges are not inferred
        (each subrepo's graph is canonical for its own code). Like every
        name-matched CALLS reach it can over-count: a listed callee may
        belong to a same-named function elsewhere. `truncated` is set when
        the fan-out or total cap was hit.

        For a Terraform address it returns the blocks that block references.
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
                    if "." in name:
                        # A Terraform address: the blocks it references.
                        rows = rows + [
                            {**r, "dst_name": r["dst_address"]}
                            for r in tf_used_by(conn, name, limit=_CALLEE_FANOUT_CAP)
                        ]
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
        Return all modules imported by `file_path`. Federated: the file may
        live in the parent or in any subrepo, we query all and aggregate.
        Pass a path relative to the parent's repo root or absolute.
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
        List the files in this repo's graph index, or check one file.

        Without `path`: every indexed File path containing `pattern` (all
        when empty), sorted, as `files` (the first `limit`) plus `total`.
        With `path` (relative to the repo root, or absolute): `indexed` is
        true when that exact file is in the index. Parent scope only, like
        `cgh files`; a subrepo answers from its own index. Unlike the FTS
        index, this also sees files that define no symbol.
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
        Find symbols (functions, classes, TF resources) whose NAME contains
        the query (substring match). Use it for a half-known name; for code
        described in words use fts_search. Federated, `limit` is per scope, results are
        concatenated; sort/trim downstream if needed.

        Optional `role` / `layer` filters keep only symbols whose File node
        carries that exact role / layer (empty = no filter). Useful to scope
        a search to e.g. role="router" or layer="domain".

        Optional `kinds` (comma list of function, class, tf_resource,
        tf_var, md_section; empty = all) limits the graph match to those
        kinds. `name_only=True` matches the name or section title only,
        not a TF type / kind or a section's body preview. Both are echoed
        back when set.

        Terraform kinds for `kinds`: tf_resource, tf_data, tf_module,
        tf_module_arg (a module call's input, module.m.<arg>), tf_local,
        tf_provider, tf_moved, tf_import, tf_removed, tf_var.
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
            for hit in tf_search(conn, query, limit, name_only, wanted):
                out.append(
                    {
                        "kind": hit["kind"],
                        "name": hit["name"],
                        "type": hit["type"],
                        "file": hit["file"],
                        "line": hit["start_line"],
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
        Return files related to `file_path` within `depth` import hops.
        Federated. Inter-repo edges are not modeled, each subrepo's IMPORTS
        graph is canonical for its own files. Useful for blast radius
        within a single scope; results are concatenated across scopes.
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
