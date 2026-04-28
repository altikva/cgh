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
    from codegraph.server import _get_conn, _logged_tool

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
        """
        from codegraph.pattern import pattern_search as _search

        hits, backend = _search(
            _srv._root,
            pattern=pattern,
            glob=glob,
            max_results=max_results,
            regex=regex,
            case_sensitive=case_sensitive,
        )
        return json.dumps(
            {
                "pattern": pattern,
                "glob": glob or None,
                "backend": backend,
                "total": len(hits),
                "hits": [{"file": h.file, "line": h.line, "text": h.text} for h in hits],
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def symbol_lookup(name: str) -> str:
        """
        Find where a symbol (function, class, TF resource) is defined.
        Returns file path, line range, type, and docstring snippet.
        Use this instead of grepping files.
        """
        conn = _get_conn()
        results = []

        # Functions
        r = conn.execute(
            "MATCH (f:Function) WHERE f.name = $n RETURN f.file_path, f.start_line, f.end_line, f.docstring",
            {"n": name},
        )
        for row in _rows(r):
            results.append(
                {
                    "kind": "function",
                    "file": row["f.file_path"],
                    "lines": f"{row['f.start_line']}-{row['f.end_line']}",
                    "doc": row["f.docstring"][:120] if row["f.docstring"] else "",
                }
            )

        # Classes
        r = conn.execute(
            "MATCH (c:Class) WHERE c.name = $n RETURN c.file_path, c.start_line, c.end_line, c.docstring",
            {"n": name},
        )
        for row in _rows(r):
            results.append(
                {
                    "kind": "class",
                    "file": row["c.file_path"],
                    "lines": f"{row['c.start_line']}-{row['c.end_line']}",
                    "doc": row["c.docstring"][:120] if row["c.docstring"] else "",
                }
            )

        # TF resources
        r = conn.execute(
            "MATCH (r:TFResource) WHERE r.name = $n RETURN r.file_path, r.type, r.start_line, r.end_line",
            {"n": name},
        )
        for row in _rows(r):
            results.append(
                {
                    "kind": "tf_resource",
                    "type": row["r.type"],
                    "file": row["r.file_path"],
                    "lines": f"{row['r.start_line']}-{row['r.end_line']}",
                }
            )

        # Markdown sections
        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.title CONTAINS $n "
            "RETURN s.file_path, s.start_line, s.end_line, s.body_preview, s.anchor",
            {"n": name},
        )
        for row in _rows(r):
            results.append(
                {
                    "kind": "md_section",
                    "file": row["s.file_path"],
                    "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                    "doc": row["s.body_preview"][:120] if row["s.body_preview"] else "",
                    "anchor": row["s.anchor"],
                }
            )

        if not results:
            return json.dumps({"found": False, "name": name})
        return json.dumps({"found": True, "name": name, "definitions": results}, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_callers(fn_name: str) -> str:
        """
        Find all functions that call `fn_name`.
        Returns a list of (caller_function, file, line).
        """
        conn = _get_conn()
        r = conn.execute(
            """MATCH (caller:Function)-[:CALLS]->(callee:Function)
               WHERE callee.name = $n
               RETURN caller.name, caller.file_path, caller.start_line""",
            {"n": fn_name},
        )
        rows = _rows(r)
        if not rows:
            return json.dumps({"fn": fn_name, "callers": []})
        callers = [
            {"caller": row["caller.name"], "file": row["caller.file_path"], "line": row["caller.start_line"]}
            for row in rows
        ]
        return json.dumps({"fn": fn_name, "callers": callers}, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_callees(fn_name: str) -> str:
        """
        Find all functions that `fn_name` calls.
        Returns a list of (callee_function, file, line).
        """
        conn = _get_conn()
        r = conn.execute(
            """MATCH (caller:Function)-[:CALLS]->(callee:Function)
               WHERE caller.name = $n
               RETURN callee.name, callee.file_path, callee.start_line""",
            {"n": fn_name},
        )
        rows = _rows(r)
        callees = [
            {"callee": row["callee.name"], "file": row["callee.file_path"], "line": row["callee.start_line"]}
            for row in rows
        ]
        return json.dumps({"fn": fn_name, "callees": callees}, indent=2)

    @mcp.tool()
    @_logged_tool
    def imports_of(file_path: str) -> str:
        """
        Return all modules imported by `file_path`.
        Pass a path relative to the repo root or absolute.
        """
        conn = _get_conn()
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        r = conn.execute(
            """MATCH (src:File {path:$p})-[i:IMPORTS]->(tgt:File)
               RETURN tgt.path, i.symbol""",
            {"p": file_path},
        )
        rows = _rows(r)
        imports = [{"module": row["tgt.path"], "symbol": row.get("i.symbol", "")} for row in rows]
        return json.dumps({"file": file_path, "imports": imports}, indent=2)

    @mcp.tool()
    @_logged_tool
    def search_symbols(query: str, limit: int = 20) -> str:
        """
        Fuzzy search for symbols (functions, classes, TF resources) by name.
        Uses substring match — useful when you don't know the exact name.
        """
        conn = _get_conn()
        results = []

        for label, kind in [("Function", "function"), ("Class", "class")]:
            r = conn.execute(
                f"MATCH (n:{label}) WHERE n.name CONTAINS $q RETURN n.name, n.file_path, n.start_line LIMIT $lim",
                {"q": query, "lim": limit},
            )
            for row in _rows(r):
                results.append(
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
            results.append(
                {
                    "kind": "tf_resource",
                    "name": row["r.name"],
                    "type": row["r.type"],
                    "file": row["r.file_path"],
                    "line": row["r.start_line"],
                }
            )

        # Markdown sections
        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.title CONTAINS $q OR s.body_preview CONTAINS $q "
            "RETURN s.title, s.file_path, s.start_line, s.level, s.anchor LIMIT $lim",
            {"q": query, "lim": limit},
        )
        for row in _rows(r):
            results.append(
                {
                    "kind": "md_section",
                    "name": row["s.title"],
                    "file": row["s.file_path"],
                    "line": row["s.start_line"],
                    "level": row["s.level"],
                    "anchor": row["s.anchor"],
                }
            )

        return json.dumps({"query": query, "results": results}, indent=2)

    @mcp.tool()
    @_logged_tool
    def subgraph(file_path: str, depth: int = 1) -> str:
        """
        Return files related to `file_path` within `depth` import hops.
        Useful for understanding blast radius before editing a file.
        """
        conn = _get_conn()
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        if depth == 1:
            r = conn.execute(
                """MATCH (src:File {path:$p})-[:IMPORTS]->(dep:File)
                   RETURN dep.path, dep.lang""",
                {"p": file_path},
            )
            deps = [{"file": row["dep.path"], "lang": row["dep.lang"]} for row in _rows(r)]

            r2 = conn.execute(
                """MATCH (upstream:File)-[:IMPORTS]->(src:File {path:$p})
                   RETURN upstream.path, upstream.lang""",
                {"p": file_path},
            )
            rdeps = [{"file": row["upstream.path"], "lang": row["upstream.lang"]} for row in _rows(r2)]
            return json.dumps(
                {
                    "file": file_path,
                    "depth": depth,
                    "depends_on": deps,
                    "depended_by": rdeps,
                },
                indent=2,
            )

        # depth 2 — two-hop traversal
        r = conn.execute(
            """MATCH (src:File {path:$p})-[:IMPORTS*1..2]->(dep:File)
               RETURN DISTINCT dep.path, dep.lang""",
            {"p": file_path},
        )
        deps = [{"file": row["dep.path"], "lang": row["dep.lang"]} for row in _rows(r)]
        return json.dumps({"file": file_path, "depth": depth, "reachable": deps}, indent=2)
