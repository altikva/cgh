# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP query tools — symbol_lookup, callers, callees, imports_of,
#              search_symbols, subgraph.

from __future__ import annotations

import json
import os

from codegraph.core.utils import rows as _rows


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
        # Parent — direct hit on the in-process write connection
        try:
            for item in query_fn(_get_conn()) or []:
                item["scope"] = "parent"
                all_results.append(item)
        except Exception as exc:
            warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})

        # Children — fresh RO conns, errors per child
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
        question — returns structured {file, line, text} hits so you can
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
            r = conn.execute(
                "MATCH (f:Function) WHERE f.name = $n RETURN f.file_path, f.start_line, f.end_line, f.docstring",
                {"n": name},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "function",
                        "file": row["f.file_path"],
                        "lines": f"{row['f.start_line']}-{row['f.end_line']}",
                        "doc": row["f.docstring"][:120] if row["f.docstring"] else "",
                    }
                )
            r = conn.execute(
                "MATCH (c:Class) WHERE c.name = $n RETURN c.file_path, c.start_line, c.end_line, c.docstring",
                {"n": name},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "class",
                        "file": row["c.file_path"],
                        "lines": f"{row['c.start_line']}-{row['c.end_line']}",
                        "doc": row["c.docstring"][:120] if row["c.docstring"] else "",
                    }
                )
            r = conn.execute(
                "MATCH (r:TFResource) WHERE r.name = $n RETURN r.file_path, r.type, r.start_line, r.end_line",
                {"n": name},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "tf_resource",
                        "type": row["r.type"],
                        "file": row["r.file_path"],
                        "lines": f"{row['r.start_line']}-{row['r.end_line']}",
                    }
                )
            r = conn.execute(
                "MATCH (s:MdSection) WHERE s.title CONTAINS $n "
                "RETURN s.file_path, s.start_line, s.end_line, s.body_preview, s.anchor",
                {"n": name},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "md_section",
                        "file": row["s.file_path"],
                        "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                        "doc": row["s.body_preview"][:120] if row["s.body_preview"] else "",
                        "anchor": row["s.anchor"],
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
        Find all functions that call `fn_name`. Federated across subrepos —
        each result tagged with `scope`. Note: cross-repo CALLS edges are
        not inferred (each subrepo's graph is canonical for its own code).
        """

        def query(conn):
            r = conn.execute(
                """MATCH (caller:Function)-[:CALLS]->(callee:Function)
                   WHERE callee.name = $n
                   RETURN caller.name, caller.file_path, caller.start_line""",
                {"n": fn_name},
            )
            return [
                {
                    "caller": row["caller.name"],
                    "file": row["caller.file_path"],
                    "line": row["caller.start_line"],
                }
                for row in _rows(r)
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
            r = conn.execute(
                """MATCH (caller:Function)-[:CALLS]->(callee:Function)
                   WHERE caller.name = $n
                   RETURN callee.name, callee.file_path, callee.start_line""",
                {"n": fn_name},
            )
            return [
                {
                    "callee": row["callee.name"],
                    "file": row["callee.file_path"],
                    "line": row["callee.start_line"],
                }
                for row in _rows(r)
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
        live in the parent or in any subrepo — we query all and aggregate.
        Pass a path relative to the parent's repo root or absolute.
        """
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        def query(conn):
            r = conn.execute(
                """MATCH (src:File {path:$p})-[i:IMPORTS]->(tgt:File)
                   RETURN tgt.path, i.symbol""",
                {"p": file_path},
            )
            return [{"module": row["tgt.path"], "symbol": row.get("i.symbol", "")} for row in _rows(r)]

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
        Uses substring match. Federated — `limit` is per scope, results are
        concatenated; sort/trim downstream if needed.
        """

        def run(conn):
            out = []
            for label, kind in [("Function", "function"), ("Class", "class")]:
                r = conn.execute(
                    f"MATCH (n:{label}) WHERE n.name CONTAINS $q RETURN n.name, n.file_path, n.start_line LIMIT $lim",
                    {"q": query, "lim": limit},
                )
                for row in _rows(r):
                    out.append(
                        {
                            "kind": kind,
                            "name": row["n.name"],
                            "file": row["n.file_path"],
                            "line": row["n.start_line"],
                        }
                    )
            r = conn.execute(
                "MATCH (r:TFResource) WHERE r.name CONTAINS $q OR r.type CONTAINS $q "
                "RETURN r.name, r.type, r.file_path, r.start_line LIMIT $lim",
                {"q": query, "lim": limit},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "tf_resource",
                        "name": row["r.name"],
                        "type": row["r.type"],
                        "file": row["r.file_path"],
                        "line": row["r.start_line"],
                    }
                )
            r = conn.execute(
                "MATCH (s:MdSection) WHERE s.title CONTAINS $q OR s.body_preview CONTAINS $q "
                "RETURN s.title, s.file_path, s.start_line, s.level, s.anchor LIMIT $lim",
                {"q": query, "lim": limit},
            )
            for row in _rows(r):
                out.append(
                    {
                        "kind": "md_section",
                        "name": row["s.title"],
                        "file": row["s.file_path"],
                        "line": row["s.start_line"],
                        "level": row["s.level"],
                        "anchor": row["s.anchor"],
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
        Federated. Inter-repo edges are not modeled — each subrepo's IMPORTS
        graph is canonical for its own files. Useful for blast radius
        within a single scope; results are concatenated across scopes.
        """
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        if depth == 1:

            def run_deps(conn):
                r = conn.execute(
                    """MATCH (src:File {path:$p})-[:IMPORTS]->(dep:File)
                       RETURN dep.path, dep.lang""",
                    {"p": file_path},
                )
                return [{"kind": "depends_on", "file": row["dep.path"], "lang": row["dep.lang"]} for row in _rows(r)]

            def run_rdeps(conn):
                r = conn.execute(
                    """MATCH (upstream:File)-[:IMPORTS]->(src:File {path:$p})
                       RETURN upstream.path, upstream.lang""",
                    {"p": file_path},
                )
                return [
                    {"kind": "depended_by", "file": row["upstream.path"], "lang": row["upstream.lang"]}
                    for row in _rows(r)
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

        # depth 2 — two-hop traversal
        def run_reach(conn):
            r = conn.execute(
                """MATCH (src:File {path:$p})-[:IMPORTS*1..2]->(dep:File)
                   RETURN DISTINCT dep.path, dep.lang""",
                {"p": file_path},
            )
            return [{"file": row["dep.path"], "lang": row["dep.lang"]} for row in _rows(r)]

        deps, warnings = _federate_kuzu(run_reach)
        payload = {"file": file_path, "depth": depth, "reachable": deps}
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)
