# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools for Claude Code plan files: plan_search + plan_list.

from __future__ import annotations

import json


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def plan_search(query: str, limit: int = 10) -> str:
        """
        BM25 search over Claude Code plan files (~/.claude/plans/*.md).

        Use when the user hints at a past plan ("the refactor we planned",
        "my last codegraph plan", "that stats feature plan") to surface
        the relevant plan file without reading the whole directory.

        Args:
            query: keywords or natural-language description
            limit: max hits (default 10)
        """
        from codegraph.core.fts import get_fts_conn
        from codegraph.core.fts import plan_search as _search

        conn = get_fts_conn(_srv._root)
        hits = _search(conn, query, limit=limit)
        return json.dumps(
            {
                "query": query,
                "total": len(hits),
                "hits": [
                    {
                        "path": h.path,
                        "slug": h.slug,
                        "agent_id": h.agent_id,
                        "title": h.title,
                        "snippet": h.snippet,
                        "score": round(h.score, 4),
                    }
                    for h in hits
                ],
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def plan_list(agent_only: bool = False, limit: int = 50) -> str:
        """
        List known plan files, newest first.

        Args:
            agent_only: if true, return only sub-agent plans (those with
                        an `-agent-<hash>` suffix). Useful to inspect
                        plans produced by background explorer/planner
                        agents.
            limit: max entries (default 50)
        """
        from codegraph.core.fts import get_fts_conn, list_plan_entries

        conn = get_fts_conn(_srv._root)
        hits = list_plan_entries(conn, agent_only=agent_only, limit=limit)
        return json.dumps(
            {
                "agent_only": agent_only,
                "total": len(hits),
                "entries": [
                    {
                        "path": h.path,
                        "slug": h.slug,
                        "agent_id": h.agent_id,
                        "title": h.title,
                        "mtime": h.score,
                    }
                    for h in hits
                ],
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def plan_rescan() -> str:
        """
        Re-scan the plans directory and refresh the FTS index. Call this
        after creating / editing a plan if the watcher hasn't caught it.
        """
        from codegraph.claude_state.plans import scan_plan_dir

        stats = scan_plan_dir(_srv._root, verbose=False)
        return json.dumps(stats, indent=2)
