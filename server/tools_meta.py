# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP meta tools — fts_search, dead_code, context_for_task, call_stats.

from __future__ import annotations

import json


def register(mcp) -> None:
    """Register meta tools on the given FastMCP instance."""
    from codegraph.server import _get_conn, _get_fts, _logged_tool, _root

    @mcp.tool()
    @_logged_tool
    def fts_search(query: str, limit: int = 15, kind: str = "") -> str:
        """
        Full-text search over symbol names AND docstrings using BM25 ranking.
        Much more powerful than symbol_lookup — handles partial names, keywords
        from docstrings, camelCase/snake_case splitting.

        Args:
            query: natural language or partial symbol name
            limit: max results (default 15)
            kind: optional filter — "function", "class", "tf_resource", "tf_var"
        """
        from codegraph.fts import fts_search as _fts

        results = _fts(
            _get_fts(),
            query,
            limit=limit,
            kind_filter=kind if kind else None,
        )
        if not results:
            return json.dumps({"query": query, "results": []})
        out = [
            {
                "kind": r.kind,
                "name": r.name,
                "file": r.file_path,
                "lines": f"{r.start_line}-{r.end_line}" if r.end_line else str(r.start_line),
                "doc": r.docstring,
                "score": round(r.score, 4),
            }
            for r in results
        ]
        return json.dumps({"query": query, "results": out}, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_dead_code(
        file_path: str = "",
        include_private: bool = False,
    ) -> str:
        """
        Find potentially unused functions, classes, and Terraform resources.
        A symbol is flagged when no CALLS / INHERITS / TF_DEPENDS edge points to it
        and it is not a known entry-point name (__init__, main, etc.).

        Args:
            file_path: optional path substring to restrict analysis to one file
            include_private: include _private functions (default False)

        Returns list of dead symbols with file + line references.
        """
        from codegraph.dead_code import find_dead_code as _find_dead

        dead = _find_dead(
            _get_conn(),
            include_private=include_private,
            file_filter=file_path if file_path else None,
        )
        if not dead:
            return json.dumps({"dead_symbols": [], "count": 0})
        out = [
            {
                "kind": d.kind,
                "name": d.name,
                "file": d.file_path,
                "lines": f"{d.start_line}-{d.end_line}",
                "reason": d.reason,
            }
            for d in dead
        ]
        return json.dumps({"dead_symbols": out, "count": len(out)}, indent=2)

    @mcp.tool()
    @_logged_tool
    def context_for_task(task: str, max_nodes: int = 15) -> str:
        """
        THE FIRST TOOL TO CALL for any coding task.
        Given a natural-language task description, builds a compact, ranked context
        block containing the most relevant symbols, their docstrings, and their
        graph relationships — WITHOUT reading any files.

        Use this before any file reads. It will cut exploration tokens by 60-90%.

        Args:
            task: description of what you need to do
                  e.g. "fix the authentication token validation logic"
                       "add a new GCS bucket resource"
                       "refactor the DataLoader class"
            max_nodes: max symbols to include (default 15)

        Returns structured markdown context ready for Claude to use directly.
        """
        from codegraph.context_builder import context_for_task as _ctx
        from codegraph.context_builder import render_context_markdown

        ctx = _ctx(
            task=task,
            kuzu_conn=_get_conn(),
            fts_conn=_get_fts(),
            max_nodes=max_nodes,
        )
        md = render_context_markdown(ctx)
        return json.dumps(
            {
                "context_markdown": md,
                "files_referenced": ctx.files_referenced,
                "symbol_count": len(ctx.nodes),
                "memory_hits": len(ctx.memory_hits),
                "estimated_tokens": ctx.token_estimate,
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def call_stats() -> str:
        """
        Show codegraph usage statistics: total calls, per-tool breakdown,
        latency percentiles, error rate, and recent calls.
        """
        from codegraph.call_log import get_stats

        stats = get_stats(_root)
        return json.dumps(stats, indent=2)
