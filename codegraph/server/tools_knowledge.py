# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools for codegraph's knowledge store: record, search,
#              list, terms (glossary), forget, and compact_session.
#              Lets Claude persist distilled insights across sessions.

from __future__ import annotations

import json


def _digest_tags(caller_tags: str) -> str:
    """Tags for a session digest. Always carries 'session-digest' so resume's
    digest bucket finds it, appending the caller's topics rather than letting
    them replace the marker. A digest tagged with only the caller's topics used
    to land under knowledge, and the agent reported its digest missing."""
    caller = (caller_tags or "").strip()
    final = caller or "compaction"
    if "session-digest" not in final:
        final = f"{final},session-digest"
    return final


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
        How do I save what I just learned for future sessions?
        Call when you find a pattern, decision, gotcha, user style or term
        worth keeping. kind: pattern | decision | gotcha | style | glossary
        | note | standing_instruction (durable user rules, lead every resume
        bundle). tags: short, comma-separated, they form the glossary.
        file_refs: paths involved. supersedes: id of an entry this
        replaces.
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
        Was this problem solved or decided before?
        Searches the NOTES saved in earlier sessions (decisions, gotchas,
        papercuts, conventions), not the code. An empty result says nothing
        about the code: to find code use symbol_lookup, fts_search,
        pattern_search or context_for_task. kind filters the note type;
        scope="all" adds federated subrepos' notes, scope-tagged.
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
        What notes were saved in earlier sessions?
        Browses saved notes newest first, not the code; filter by kind, tag
        or session_id, page with offset (`has_more`, `next_offset`). Code:
        fts_search, symbol_lookup. Promoted entries carry `provenance`.
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
        # Fewer than a full page back means the list is exhausted, whatever a
        # (now-aligned) count says, so require a full page AND more counted.
        has_more = len(entries) == limit and (offset + len(entries)) < total
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
        Which topics do the saved notes cover?
        Returns every knowledge tag with its count, most used first. Use it
        to see what is recorded before a knowledge_search.
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
        """
        How do I delete a saved note?
        Deletes one knowledge entry by `entry_id` (from knowledge_search or
        knowledge_list). To replace an entry, prefer knowledge_record with
        supersedes.
        """
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
        How do I keep a summary of this whole session as a note?
        Saves `digest` as a knowledge entry (kind=note) stamped with
        session_id, before the client compacts the conversation; find it
        later with knowledge_list(session_id=...). Task state for the next
        session: checkpoint.
        """
        from codegraph.state.call_log import knowledge_record as _record

        entry_id = _record(
            title=title or f"Session digest {session_id}",
            body=digest,
            kind="note",
            tags=_digest_tags(tags),
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
