# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP docs tools — search_docs, doc_outline, doc_refs.

from __future__ import annotations

import json
import os

from codegraph.core.utils import rows as _rows


def register(mcp) -> None:
    """Register documentation tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _logged_tool

    @mcp.tool()
    @_logged_tool
    def search_docs(query: str, limit: int = 10) -> str:
        """
        Search documentation (Markdown files) by heading title or body content.
        Returns matching sections with file path, line range, and body preview.
        Use this to find relevant documentation before diving into code.
        """
        conn = _get_conn()
        results = []

        # Search by title
        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.title CONTAINS $q "
            "RETURN s.title, s.level, s.file_path, s.start_line, s.end_line, "
            "s.body_preview, s.anchor LIMIT $lim",
            {"q": query, "lim": limit},
        )
        seen_ids = set()
        for row in _rows(r):
            key = f"{row['s.file_path']}:{row['s.start_line']}"
            if key not in seen_ids:
                seen_ids.add(key)
                results.append(
                    {
                        "title": row["s.title"],
                        "level": row["s.level"],
                        "file": row["s.file_path"],
                        "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                        "preview": row["s.body_preview"][:200] if row["s.body_preview"] else "",
                        "anchor": row["s.anchor"],
                    }
                )

        # Search by body content (only if title search didn't fill limit)
        if len(results) < limit:
            remaining = limit - len(results)
            r = conn.execute(
                "MATCH (s:MdSection) WHERE s.body_preview CONTAINS $q "
                "RETURN s.title, s.level, s.file_path, s.start_line, s.end_line, "
                "s.body_preview, s.anchor LIMIT $lim",
                {"q": query, "lim": remaining},
            )
            for row in _rows(r):
                key = f"{row['s.file_path']}:{row['s.start_line']}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    results.append(
                        {
                            "title": row["s.title"],
                            "level": row["s.level"],
                            "file": row["s.file_path"],
                            "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                            "preview": row["s.body_preview"][:200] if row["s.body_preview"] else "",
                            "anchor": row["s.anchor"],
                        }
                    )

        return json.dumps({"query": query, "results": results, "count": len(results)}, indent=2)

    @mcp.tool()
    @_logged_tool
    def doc_outline(file_path: str) -> str:
        """
        Return the heading outline (table of contents) of a Markdown file.
        Shows the hierarchical structure of sections with line numbers.
        """
        conn = _get_conn()
        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.file_path = $p "
            "RETURN s.title, s.level, s.start_line, s.end_line, s.anchor "
            "ORDER BY s.start_line",
            {"p": file_path},
        )
        rows = _rows(r)
        if not rows:
            return json.dumps({"file": file_path, "outline": [], "note": "No sections found"})

        outline = []
        for row in rows:
            indent = "  " * (row["s.level"] - 1)
            outline.append(
                {
                    "title": row["s.title"],
                    "level": row["s.level"],
                    "line": row["s.start_line"],
                    "end_line": row["s.end_line"],
                    "anchor": row["s.anchor"],
                    "display": f"{indent}{'#' * row['s.level']} {row['s.title']} (L{row['s.start_line']})",
                }
            )

        return json.dumps({"file": file_path, "outline": outline}, indent=2)

    @mcp.tool()
    @_logged_tool
    def doc_refs(symbol_name: str) -> str:
        """
        Find all Markdown documentation that references a code symbol.
        Use this to find docs about a function or class before reading its code.
        """
        conn = _get_conn()
        results = []

        # Via MD_REFS_SYMBOL edges (function references)
        r = conn.execute(
            """MATCH (s:MdSection)-[r:MD_REFS_SYMBOL]->(fn:Function)
               WHERE fn.name = $n
               RETURN s.title, s.file_path, s.start_line, s.end_line, r.context""",
            {"n": symbol_name},
        )
        for row in _rows(r):
            results.append(
                {
                    "section": row["s.title"],
                    "file": row["s.file_path"],
                    "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                    "ref_type": "function",
                    "context": row["r.context"],
                }
            )

        # Via MD_REFS_CLASS edges (class references)
        r = conn.execute(
            """MATCH (s:MdSection)-[r:MD_REFS_CLASS]->(c:Class)
               WHERE c.name = $n
               RETURN s.title, s.file_path, s.start_line, s.end_line, r.context""",
            {"n": symbol_name},
        )
        for row in _rows(r):
            results.append(
                {
                    "section": row["s.title"],
                    "file": row["s.file_path"],
                    "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                    "ref_type": "class",
                    "context": row["r.context"],
                }
            )

        # Also search body_preview for mentions
        r = conn.execute(
            "MATCH (s:MdSection) WHERE s.body_preview CONTAINS $n "
            "RETURN s.title, s.file_path, s.start_line, s.end_line LIMIT 10",
            {"n": symbol_name},
        )
        seen = {(r_item["file"], r_item["lines"]) for r_item in results}
        for row in _rows(r):
            key = (row["s.file_path"], f"{row['s.start_line']}-{row['s.end_line']}")
            if key not in seen:
                seen.add(key)
                results.append(
                    {
                        "section": row["s.title"],
                        "file": row["s.file_path"],
                        "lines": f"{row['s.start_line']}-{row['s.end_line']}",
                        "ref_type": "text_mention",
                        "context": "body",
                    }
                )

        return json.dumps(
            {
                "symbol": symbol_name,
                "doc_references": results,
                "count": len(results),
            },
            indent=2,
        )
