# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools for Claude Code memory — memory_search + memory_list.

from __future__ import annotations

import json


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def memory_search(query: str, kind: str = "", limit: int = 10) -> str:
        """
        BM25 search over Claude Code memory entries (the same auto-memory
        system at ~/.claude/projects/<slug>/memory/).

        Use this BEFORE asking the user about preferences / past decisions.
        Results include the file path so you can read the full entry if
        needed — but the snippet is usually enough.

        Args:
            query:  keywords or natural language
            kind:   optional filter — user / feedback / project / reference
            limit:  max hits (default 10)
        """
        from codegraph.fts import get_fts_conn
        from codegraph.fts import memory_search as _search

        conn = get_fts_conn(_srv._root)
        hits = _search(conn, query, kind=kind or None, limit=limit)
        return json.dumps(
            {
                "query": query,
                "kind": kind or None,
                "total": len(hits),
                "hits": [
                    {
                        "path": h.path,
                        "kind": h.kind,
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
    def memory_list(kind: str = "") -> str:
        """
        List every memory entry known to codegraph, newest first.
        Cheap browse — doesn't touch FTS. Useful to see what's been
        recorded about the user / project.

        Args:
            kind: optional filter (user / feedback / project / reference)
        """
        from codegraph.fts import get_fts_conn, list_memory_entries

        conn = get_fts_conn(_srv._root)
        hits = list_memory_entries(conn, kind=kind or None)
        return json.dumps(
            {
                "kind": kind or None,
                "total": len(hits),
                "entries": [
                    {
                        "path": h.path,
                        "kind": h.kind,
                        "title": h.title,
                        "mtime": h.score,  # list_memory_entries packs mtime into score
                        "snippet": h.snippet[:160],
                    }
                    for h in hits
                ],
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def memory_rescan() -> str:
        """
        Re-scan the memory directory and refresh the FTS index. Call this
        after adding / editing memory files if the watcher hasn't caught
        them yet.
        """
        from codegraph.memory_index import scan_memory_dir

        stats = scan_memory_dir(_srv._root, verbose=False)
        return json.dumps(stats, indent=2)
