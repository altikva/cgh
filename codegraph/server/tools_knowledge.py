# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools for codegraph's knowledge store: record, search,
#              list, terms (glossary), forget, and compact_session.
#              Lets Claude persist distilled insights across sessions.

from __future__ import annotations

import json


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def knowledge_record(
        title: str,
        body: str,
        kind: str = "note",
        tags: str = "",
        file_refs: str = "",
        session_id: str = "",
        supersedes: int = 0,
    ) -> str:
        """
        Persist a distilled knowledge entry so future sessions can recall it.

        Use this when you notice something worth remembering:
          - a pattern the codebase follows ("all handlers return X")
          - a decision and its rationale ("we chose Stripe over PayPal because…")
          - a gotcha ("Kuzu holds the DB lock until Python GC runs")
          - a user style preference ("user prefers French in commit bodies")
          - a glossary entry ("RFM = Recency, Frequency, Monetary")

        kind: pattern | decision | gotcha | style | glossary | note |
              standing_instruction (durable rules and corrections from the
              user; these lead every resume bundle)
        tags: comma-separated, short. These ARE the glossary index.
        file_refs: comma-separated canonical file paths involved.
        session_id: optional, stamps the entry with the current session.
        supersedes: optional id of an entry this one replaces; the old
              entry stops appearing in searches and bundles.
        """
        from codegraph.state.call_log import knowledge_record as _record

        entry_id = _record(
            title=title,
            body=body,
            kind=kind,
            tags=tags,
            file_refs=file_refs,
            session_id=session_id,
            repo_root=_srv._root,
            supersedes=supersedes,
        )
        try:
            from codegraph.state.activity import log as _log

            _log(
                _srv._root,
                "knowledge_record",
                f"id={entry_id} kind={kind} title={title[:60]}",
            )
        except Exception:
            pass
        return json.dumps({"id": entry_id, "kind": kind, "title": title})

    @mcp.tool()
    @_logged_tool
    def knowledge_search(
        query: str, kind: str = "", limit: int = 10, scope: str = ""
    ) -> str:
        """
        BM25 search over persisted knowledge. Use when facing a problem
        you suspect has been solved before, or when looking up a pattern
        by keyword / tag.

        kind: optional filter, pattern/decision/gotcha/style/glossary/
              note/standing_instruction
        scope: default searches THIS repo only. Pass "all" to also
              search federated subrepos' knowledge read-only; their hits
              come back tagged with a `scope` field. Never on by
              default: cross-repo learnings load only when asked.
        """
        from codegraph.state.call_log import knowledge_search as _search

        hits = _search(query, kind=kind or None, limit=limit, repo_root=_srv._root)
        if scope == "all" and _srv._root is not None:
            from codegraph.analysis.federation import resolve_children
            from codegraph.state.call_log import knowledge_search_ro

            for child in resolve_children(_srv._root):
                for row in knowledge_search_ro(
                    child / ".codegraph" / "call_log.db",
                    query,
                    kind=kind or None,
                    limit=limit,
                ):
                    row["scope"] = child.name
                    hits.append(row)
        return json.dumps(
            {"query": query, "kind": kind or None, "total": len(hits), "hits": hits},
            indent=2,
            default=str,
        )

    @mcp.tool()
    @_logged_tool
    def knowledge_list(
        kind: str = "",
        tag: str = "",
        session_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """
        Browse knowledge entries, newest first. Useful at session kickoff
        to see what's already known before re-deriving.

        Pagination: pass `offset` to page through results. Response includes
        `total`, `returned`, `has_more`, and `next_offset` so the caller can
        follow up without counting locally.
        """
        from codegraph.state.call_log import knowledge_count
        from codegraph.state.call_log import knowledge_list as _list

        entries = _list(
            kind=kind or None,
            tag=tag or None,
            session_id=session_id or None,
            limit=limit,
            offset=offset,
            repo_root=_srv._root,
        )
        total = knowledge_count(
            kind=kind or None,
            tag=tag or None,
            session_id=session_id or None,
            repo_root=_srv._root,
        )
        has_more = (offset + len(entries)) < total
        return json.dumps(
            {
                "kind": kind or None,
                "tag": tag or None,
                "session_id": session_id or None,
                "total": total,
                "offset": offset,
                "limit": limit,
                "returned": len(entries),
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
                "entries": entries,
            },
            indent=2,
            default=str,
        )

    @mcp.tool()
    @_logged_tool
    def knowledge_terms(min_count: int = 1) -> str:
        """
        Return the knowledge glossary, every tag with its occurrence
        count, sorted by frequency. Use this to discover what topics
        have been captured without reading individual entries.
        """
        from codegraph.state.call_log import knowledge_terms as _terms

        terms = _terms(min_count=min_count, repo_root=_srv._root)
        return json.dumps(
            {
                "total": len(terms),
                "terms": [{"term": t, "count": n} for t, n in terms],
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def knowledge_forget(entry_id: int) -> str:
        """Delete a single knowledge entry by id (obtained from search/list)."""
        from codegraph.state.call_log import knowledge_forget as _forget

        ok = _forget(entry_id=entry_id, repo_root=_srv._root)
        return json.dumps({"id": entry_id, "forgotten": ok})

    @mcp.tool()
    @_logged_tool
    def compact_session(
        session_id: str,
        title: str,
        digest: str,
        tags: str = "",
        file_refs: str = "",
    ) -> str:
        """
        Context-compaction helper: save a distilled summary of the current
        session as a knowledge entry (kind='note'). Call this BEFORE the
        client truncates / summarizes the conversation so the digest
        survives into future sessions.

        The session_id is stamped on the entry so you can later filter
        with knowledge_list(session_id=...).
        """
        from codegraph.state.call_log import knowledge_record as _record

        entry_id = _record(
            title=title or f"Session digest {session_id}",
            body=digest,
            kind="note",
            tags=tags or "compaction,session-digest",
            file_refs=file_refs,
            session_id=session_id,
            repo_root=_srv._root,
        )
        try:
            from codegraph.state.activity import log as _log

            _log(_srv._root, "session_compact", f"{session_id} id={entry_id}")
        except Exception:
            pass
        return json.dumps(
            {
                "id": entry_id,
                "session_id": session_id,
                "title": title or f"Session digest {session_id}",
            }
        )
