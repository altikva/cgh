# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools to fetch a URL into the searchable index and
#              query it. A fetch is gated network egress (http/https,
#              no private hosts, off unless allow_fetch = true,
#              always audited); search_fetched then reads it back with
#              zero further network. Caches by URL with a TTL.

from __future__ import annotations

import json


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def fetch_and_index(url: str, ttl_hours: float = 24.0, force: bool = False) -> str:
        """
        How do I make a web page searchable offline?
        Fetches an http/https `url`, turns it into text and indexes it for
        search_fetched. Refused unless `[codegraph] allow_fetch = true` is
        set; private, loopback and link-local hosts are refused; every
        fetch is audited. A re-fetch within ttl_hours returns the cached
        count unless force=True.
        """
        from codegraph.analysis.fetch_index import FetchError
        from codegraph.analysis.fetch_index import fetch_and_index as _fi
        from codegraph.core.config import load_config

        cfg = {"allow_fetch": load_config(_srv._root).allow_fetch}
        try:
            out = _fi(_srv._root, url, ttl_hours=ttl_hours, force=force, config=cfg)
        except FetchError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(out)

    @mcp.tool()
    @_logged_tool
    def search_fetched(query: str, limit: int = 10) -> str:
        """
        What did the pages I fetched say about this?
        Searches pages indexed by fetch_and_index; no network. Returns url,
        title, snippet. Repo docs: search_docs.
        """
        from codegraph.analysis.fetch_index import search_fetched as _sf

        return json.dumps({"results": _sf(_srv._root, query, limit=limit)})
