# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP docs tools: search_docs, doc_outline, doc_refs.

from __future__ import annotations

import json
import os


def register(mcp) -> None:
    """Register documentation tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import federate_scoped
    from codegraph.server import _get_conn, _logged_tool

    def _query_each(query_fn):
        """Parent + children fan-out, [(scope, payload), …]. See federate_scoped."""
        scoped, _warnings = federate_scoped(_get_conn, _srv._root, query_fn)
        return scoped

    @mcp.tool()
    @_logged_tool
    def search_docs(query: str, limit: int = 10) -> str:
        """
        Which Markdown doc covers this topic?
        Searches doc headings and bodies; returns sections with file, line
        range and a preview. Use before diving into code when docs may
        explain it. Federated. Outline of one file: doc_outline. Docs naming
        a symbol: doc_refs.
        """

        q_str = query  # capture before shadowing

        section_fields = [
            "title",
            "level",
            "file_path",
            "start_line",
            "end_line",
            "body_preview",
            "anchor",
        ]

        def _format(row):
            return {
                "title": row["title"],
                "level": row["level"],
                "file": row["file_path"],
                "lines": f"{row['start_line']}-{row['end_line']}",
                "preview": (row.get("body_preview") or "")[:200],
                "anchor": row["anchor"],
            }

        def run(conn):
            out: list[dict] = []
            seen_ids: set[str] = set()
            for row in conn.find_nodes(
                "MdSection",
                contains={"title": q_str},
                return_fields=section_fields,
                limit=limit,
            ):
                key = f"{row['file_path']}:{row['start_line']}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                out.append(_format(row))
            if len(out) < limit:
                for row in conn.find_nodes(
                    "MdSection",
                    contains={"body_preview": q_str},
                    return_fields=section_fields,
                    limit=limit - len(out),
                ):
                    key = f"{row['file_path']}:{row['start_line']}"
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    out.append(_format(row))
            return out

        results: list[dict] = []
        for scope, rows in _query_each(run):
            for row in rows:
                row["scope"] = scope
                results.append(row)
        return json.dumps(
            {"query": q_str, "results": results, "count": len(results)}, indent=2
        )

    @mcp.tool()
    @_logged_tool
    def doc_outline(file_path: str) -> str:
        """
        What sections does this Markdown file have?
        Returns the heading tree of `file_path` with line numbers, so you
        Read only the section you need. Finding the right doc: search_docs.
        """
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        def query(conn):
            return conn.find_nodes(
                "MdSection",
                where={"file_path": file_path},
                return_fields=["title", "level", "start_line", "end_line", "anchor"],
                order_by=["start_line"],
            )

        outline: list[dict] = []
        for scope, rows in _query_each(query):
            for row in rows:
                indent = "  " * (row["level"] - 1)
                outline.append(
                    {
                        "scope": scope,
                        "title": row["title"],
                        "level": row["level"],
                        "line": row["start_line"],
                        "end_line": row["end_line"],
                        "anchor": row["anchor"],
                        "display": f"{indent}{'#' * row['level']} {row['title']} (L{row['start_line']})",
                    }
                )
        if not outline:
            return json.dumps(
                {"file": file_path, "outline": [], "note": "No sections found"}
            )
        return json.dumps({"file": file_path, "outline": outline}, indent=2)

    @mcp.tool()
    @_logged_tool
    def doc_refs(symbol_name: str) -> str:
        """
        Which docs mention this symbol?
        Returns the Markdown sections that reference `symbol_name`. Use it
        to find the prose about a function or class before reading its
        code. Code that calls it: find_callers.
        """

        def query(conn):
            out: list[dict] = []
            # find_neighbors anchored on dst (the Function/Class side),
            # returning src (MdSection) fields + edge context.
            for row in conn.find_neighbors(
                "MD_REFS_SYMBOL",
                dst_where={"name": symbol_name},
                return_src=["title", "file_path", "start_line", "end_line"],
                return_edge=["context"],
            ):
                out.append(
                    {
                        "section": row["src_title"],
                        "file": row["src_file_path"],
                        "lines": f"{row['src_start_line']}-{row['src_end_line']}",
                        "ref_type": "function",
                        "context": row.get("edge_context") or "",
                    }
                )
            for row in conn.find_neighbors(
                "MD_REFS_CLASS",
                dst_where={"name": symbol_name},
                return_src=["title", "file_path", "start_line", "end_line"],
                return_edge=["context"],
            ):
                out.append(
                    {
                        "section": row["src_title"],
                        "file": row["src_file_path"],
                        "lines": f"{row['src_start_line']}-{row['src_end_line']}",
                        "ref_type": "class",
                        "context": row.get("edge_context") or "",
                    }
                )
            seen = {(it["file"], it["lines"]) for it in out}
            for row in conn.find_nodes(
                "MdSection",
                contains={"body_preview": symbol_name},
                return_fields=["title", "file_path", "start_line", "end_line"],
                limit=10,
            ):
                key = (row["file_path"], f"{row['start_line']}-{row['end_line']}")
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "section": row["title"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "ref_type": "text_mention",
                        "context": "body",
                    }
                )
            return out

        results: list[dict] = []
        for scope, rows in _query_each(query):
            for r in rows:
                r["scope"] = scope
                results.append(r)
        return json.dumps(
            {
                "symbol": symbol_name,
                "doc_references": results,
                "count": len(results),
            },
            indent=2,
        )
