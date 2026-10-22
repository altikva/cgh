# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools for codegraph's knowledge store: record, search,
#              list, terms (glossary), forget, outcome, and compact_session.
#              Lets Claude persist distilled insights across sessions and
#              record whether relying on them worked.

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
        Persist a distilled knowledge entry so future sessions can recall it.

        Use this when you notice something worth remembering:
          - a pattern the codebase follows ("all handlers return X")
          - a decision and its rationale ("we chose Stripe over PayPal because…")
          - a gotcha ("DuckDB rejects a RO open while a RW conn is live")
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
        Search the NOTES saved by agents and people in earlier sessions
        (decisions, gotchas, papercuts, conventions), not the code. Use it
        when facing a problem you suspect was solved before. To find code,
        use symbol_lookup, search_symbols, fts_search (code described in
        words), pattern_search or context_for_task instead: an empty
        result here says nothing about what the code contains.

        kind: optional filter, pattern/decision/gotcha/style/glossary/
              note/standing_instruction
        scope: default searches THIS repo only. Pass "all" to also
              search federated subrepos' knowledge read-only; their hits
              come back tagged with a `scope` field. Never on by
              default: cross-repo learnings load only when asked.

        Ranking follows recorded outcomes (knowledge_outcome): entries
        that proved useful rank up, dead ends and corrected entries rank
        down, and every rated hit carries an `outcome` summary (status:
        preferred, useful, contested, dead_end, corrected or faded).
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
        Browse the notes saved in earlier sessions (not the code), newest
        first. Useful at session kickoff to see what's already known
        before re-deriving. To find code use the code tools (symbol_lookup,
        fts_search, pattern_search, context_for_task).

        Pagination: pass `offset` to page through results. Response includes
        `total`, `returned`, `has_more`, and `next_offset` so the caller can
        follow up without counting locally. An entry promoted from another
        worktree (`cgh knowledge promote`) carries a `provenance` object:
        source branch, PR, worktree, origin id, promoted_at.

        Entries with recorded outcomes are tiered first (useful, then
        unrated or contested, then dead ends, then corrected), newest
        first inside each tier, and carry an `outcome` summary.
        """
        from codegraph.server._budget import TOOL_CAPS, cap_answer, dumps_answer
        from codegraph.state.call_log import knowledge_count
        from codegraph.state.call_log import knowledge_list as _list

        entries = _list(
            kind=kind or None,
            tag=tag or None,
            session_id=session_id or None,
            limit=limit,
            offset=offset,
            repo_root=_srv._root,
            rank_outcomes=True,
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
        payload = {
            "kind": kind or None,
            "tag": tag or None,
            "session_id": session_id or None,
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(entries),
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "entries": json.loads(json.dumps(entries, default=str)),
        }
        # Cut here rather than in the shared wrapper so the page counters
        # follow: the next page starts at the first entry left out.
        spec = TOOL_CAPS["knowledge_list"]
        hint = f"Answer cut to about {spec.max_bytes // 1000} KB. {spec.hint}"
        first = payload["entries"][:1]
        if cap_answer(payload, ["entries"], hint, spec.max_bytes):
            if not payload["entries"] and first:
                # One entry alone is over the ceiling: serve it with its body
                # shortened rather than an empty page that never advances.
                entry = dict(first[0])
                entry["body"] = str(entry.get("body", ""))[: spec.max_bytes // 2]
                entry["body_clipped"] = True
                payload["entries"] = [entry]
            payload["returned"] = len(payload["entries"])
            payload["has_more"] = True
            payload["next_offset"] = offset + payload["returned"]
        return dumps_answer(payload)

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
    def knowledge_outcome(
        verdict: str,
        entry_id: int = 0,
        query: str = "",
        note: str = "",
        file_refs: str = "",
        corrected_by: int = 0,
        session_id: str = "",
    ) -> str:
        """
        Record whether relying on a knowledge entry, or on some files,
        actually worked. Call it when an entry helped you, misled you, or
        sent you down a path that went nowhere. Search, list and resume
        use these verdicts: useful entries rank up, corrected ones rank
        down and are flagged, and resume lists known dead ends so the
        next session does not retry them.

        verdict: useful | dead_end | corrected (the entry was wrong and
              has been fixed; pass corrected_by=<id of the fixing entry>)
        entry_id: the entry the verdict is about (from search/list), or
        query: pick the best knowledge_search hit instead of an id.
        file_refs: comma-separated files or symbols the verdict also
              covers ("this file was a dead end for task X"); may be the
              only target.
        note: one line on why, e.g. the task it was for.
        """
        from codegraph.state.call_log import (
            knowledge_outcome_record,
            knowledge_outcome_scores,
        )
        from codegraph.state.call_log import knowledge_search as _search

        target = int(entry_id or 0)
        title = ""
        if not target and query.strip():
            # Plain relevance: the target must not depend on earlier verdicts.
            hits = _search(query, limit=1, repo_root=_srv._root, rank_outcomes=False)
            if not hits:
                return json.dumps({"error": f"no knowledge entry matches {query!r}"})
            target, title = int(hits[0]["id"]), hits[0]["title"]
        try:
            outcome_id = knowledge_outcome_record(
                verdict=verdict,
                entry_id=target or None,
                file_refs=file_refs,
                note=note,
                corrected_by=corrected_by or None,
                session_id=session_id,
                repo_root=_srv._root,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        entries, files = knowledge_outcome_scores(repo_root=_srv._root)
        refs = [f.strip() for f in file_refs.split(",") if f.strip()]
        out: dict = {"id": outcome_id, "verdict": verdict}
        if target:
            out["entry_id"] = target
            if title:
                out["title"] = title
            if target in entries:
                out["outcome"] = entries[target].summary()
        if refs:
            out["files"] = {f: files[f].summary() for f in refs if f in files}
        return json.dumps(out, default=str)

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
