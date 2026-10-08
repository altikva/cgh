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
        Is there a plan for this already?
        Searches the agent's plan files. Use when the user hints at a past
        plan ("the refactor we planned"). All plans, newest first:
        plan_list.

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
        Which plan files exist?
        Lists plan files newest first; agent_only keeps sub-agent plans. On
        a topic: plan_search.

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
        How do I make a plan I just wrote searchable?
        Re-scans the plans directory into the search index. Rarely needed:
        the watcher does it; call only if plan_search misses a fresh plan.
        """
        from codegraph.claude_state.plans import scan_plan_dir

        stats = scan_plan_dir(_srv._root, verbose=False)
        return json.dumps(stats, indent=2)
