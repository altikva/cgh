# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP query tools: symbol_lookup, callers, callees, imports_of,
#              search_symbols, subgraph.

from __future__ import annotations

import json
import os

def register(mcp) -> None:
    """Register query tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import for_each_child_kuzu
    from codegraph.server import _get_conn, _logged_tool

    def _federate_kuzu(query_fn):
        """
        Run query_fn(conn) against the parent's write conn (in-process)
        and each federated subrepo's RO Kuzu DB. Returns:
          (results_with_scope, partial_warnings)
        Each item in results_with_scope is whatever query_fn returned, with
        a "scope" key injected. partial_warnings is a list of dicts
        {scope, error} for child DBs that couldn't be queried.
        """
        all_results: list[dict] = []
        warnings: list[dict] = []
        # Parent, direct hit on the in-process write connection
        try:
            for item in query_fn(_get_conn()) or []:
                item["scope"] = "parent"
                all_results.append(item)
        except Exception as exc:
            warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})

        # Children, fresh RO conns, errors per child
        if _srv._root is not None:
            for scoped in for_each_child_kuzu(_srv._root, lambda c, _r: query_fn(c)):
                if scoped.error:
                    warnings.append({"scope": scoped.scope, "error": scoped.error})
                    continue
                for item in scoped.payload or []:
                    item["scope"] = scoped.scope
                    all_results.append(item)
        return all_results, warnings

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
            all_hits.append({"scope": "parent", "file": h.file, "line": h.line, "text": h.text})

        # Each federated subrepo
        for child in resolve_children(_srv._root) if _srv._root else []:
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
                all_hits.append({"scope": child.name, "file": h.file, "line": h.line, "text": h.text})

        # Apply max_results across the whole federation as a soft cap
        all_hits = all_hits[: max_results * (1 + len(resolve_children(_srv._root)) if _srv._root else 1)]

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
    def symbol_lookup(name: str) -> str:
        """
        Find where a symbol (function, class, TF resource) is defined.
        Returns file path, line range, type, and docstring snippet, plus
        a `scope` tag (parent / <subrepo-name>) when federation is on.
        Use this instead of grepping files.
        """

        def query(conn):
            out = []
            for row in conn.find_nodes(
                "Function",
                where={"name": name},
                return_fields=["file_path", "start_line", "end_line", "docstring"],
            ):
                out.append(
                    {
                        "kind": "function",
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                    }
                )
            for row in conn.find_nodes(
                "Class",
                where={"name": name},
                return_fields=["file_path", "start_line", "end_line", "docstring"],
            ):
                out.append(
                    {
                        "kind": "class",
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                    }
                )
            for row in conn.find_nodes(
                "TFResource",
                where={"name": name},
                return_fields=["file_path", "type", "start_line", "end_line"],
            ):
                out.append(
                    {
                        "kind": "tf_resource",
                        "type": row["type"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                    }
                )
            for row in conn.find_nodes(
                "MdSection",
                contains={"title": name},
                return_fields=[
                    "file_path", "start_line", "end_line", "body_preview", "anchor",
                ],
            ):
                out.append(
                    {
                        "kind": "md_section",
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["body_preview"] or "")[:120],
                        "anchor": row["anchor"],
                    }
                )
            return out

        results, warnings = _federate_kuzu(query)
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
        """

        def query(conn):
            return [
                {
                    "caller": row["src_name"],
                    "file": row["src_file_path"],
                    "line": row["src_start_line"],
                }
                for row in conn.find_neighbors(
                    "CALLS",
                    dst_where={"name": fn_name},
                    return_src=["name", "file_path", "start_line"],
                )
            ]

        callers, warnings = _federate_kuzu(query)
        out = {"fn": fn_name, "callers": callers}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_callees(fn_name: str) -> str:
        """
        Find all functions that `fn_name` calls. Federated across subrepos.
        """

        def query(conn):
            return [
                {
                    "callee": row["dst_name"],
                    "file": row["dst_file_path"],
                    "line": row["dst_start_line"],
                }
                for row in conn.find_neighbors(
                    "CALLS",
                    src_where={"name": fn_name},
                    return_dst=["name", "file_path", "start_line"],
                )
            ]

        callees, warnings = _federate_kuzu(query)
        out = {"fn": fn_name, "callees": callees}
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

        imports, warnings = _federate_kuzu(query)
        out = {"file": file_path, "imports": imports}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def search_symbols(query: str, limit: int = 20) -> str:
        """
        Fuzzy search for symbols (functions, classes, TF resources) by name.
        Uses substring match. Federated, `limit` is per scope, results are
        concatenated; sort/trim downstream if needed.
        """

        def run(conn):
            out = []
            for label, kind in [("Function", "function"), ("Class", "class")]:
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
            for row in conn.find_nodes(
                "TFResource",
                contains={"name": query, "type": query},
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
            for row in conn.find_nodes(
                "MdSection",
                contains={"title": query, "body_preview": query},
                return_fields=["title", "file_path", "start_line", "level", "anchor"],
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
            return out

        results, warnings = _federate_kuzu(run)
        payload = {"query": query, "results": results}
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
                    {"kind": "depends_on", "file": row["dst_path"], "lang": row["dst_lang"]}
                    for row in conn.find_neighbors(
                        "IMPORTS", src_key=file_path, return_dst=["path", "lang"]
                    )
                ]

            def run_rdeps(conn):
                return [
                    {"kind": "depended_by", "file": row["src_path"], "lang": row["src_lang"]}
                    for row in conn.find_neighbors(
                        "IMPORTS", dst_key=file_path, return_src=["path", "lang"]
                    )
                ]

            deps_all, w1 = _federate_kuzu(run_deps)
            rdeps_all, w2 = _federate_kuzu(run_rdeps)
            depends_on = [{k: v for k, v in d.items() if k != "kind"} for d in deps_all]
            depended_by = [{k: v for k, v in d.items() if k != "kind"} for d in rdeps_all]
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
                    "IMPORTS", file_path, max_depth=int(depth), return_fields=["path", "lang"]
                )
            ]

        deps, warnings = _federate_kuzu(run_reach)
        payload = {"file": file_path, "depth": depth, "reachable": deps}
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)
