# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Session continuity in one call each. checkpoint persists a
#              session digest before a clear or compaction; resume returns
#              ONE composed, ranked, budgeted bundle: standing
#              instructions first, then recent digests, task-relevant
#              knowledge, open plans, and recent file summaries. Clearing
#              a context stops costing anything: what was learned survives
#              outside the window.

from __future__ import annotations

import json


def build_resume_bundle(
    repo_root,
    session_id: str = "",
    task: str = "",
    budget_kb: float = 16,
    scope: str = "",
) -> dict:
    """The composed bundle, priority-ordered and budget-capped. Shared by
    the resume MCP tool and the SessionStart header hook."""
    from codegraph.core.fts import get_fts_conn, list_plan_entries
    from codegraph.state.call_log import knowledge_list, knowledge_search
    from codegraph.state.findings import query_findings

    sections: list[tuple[str, list[dict]]] = []

    instructions = knowledge_list(
        kind="standing_instruction", limit=20, repo_root=repo_root
    )
    sections.append(("standing_instructions", instructions))

    digests = []
    if session_id:
        digests = knowledge_list(
            tag="session-digest", session_id=session_id, limit=2, repo_root=repo_root
        )
    digests += [
        d
        for d in knowledge_list(tag="session-digest", limit=3, repo_root=repo_root)
        if d["id"] not in {x["id"] for x in digests}
    ]
    sections.append(("digests", digests))

    knowledge = (
        knowledge_search(task, limit=8, repo_root=repo_root)
        if task
        else knowledge_list(limit=8, repo_root=repo_root)
    )
    seen_ids = {d["id"] for d in instructions} | {d["id"] for d in digests}
    sections.append(("knowledge", [k for k in knowledge if k["id"] not in seen_ids]))

    if scope == "all" and repo_root is not None:
        from codegraph.analysis.federation import resolve_children
        from codegraph.state.call_log import knowledge_search_ro

        federated: list[dict] = []
        for child in resolve_children(repo_root):
            for row in knowledge_search_ro(
                child / ".codegraph" / "call_log.db", task or "", limit=5
            ):
                row["scope"] = child.name
                federated.append(row)
        sections.append(("federated_knowledge", federated))

    plans: list[dict] = []
    try:
        for hit in list_plan_entries(get_fts_conn(repo_root), limit=5):
            plans.append({"title": hit.title, "path": hit.path, "agent": hit.agent_id})
    except Exception:
        pass
    sections.append(("open_plans", plans))

    summaries = [
        {"file": r["file"], "summary": r["value"][:400]}
        for r in query_findings(repo_root, key_prefix="summary", limit=10)
        if r["key"] == "summary"
    ][:5]
    sections.append(("recent_summaries", summaries))

    # Budget: sections in priority order, entries dropped once the
    # serialized bundle would pass the cap. Standing instructions are
    # never dropped: they are the whole point of the pillar.
    budget = int(budget_kb * 1024)
    bundle: dict = {"truncated": False}
    used = 0
    for name, entries in sections:
        kept = []
        for entry in entries:
            size = len(json.dumps(entry))
            if name != "standing_instructions" and used + size > budget:
                bundle["truncated"] = True
                break
            kept.append(entry)
            used += size
        bundle[name] = kept
    return bundle


def register(mcp) -> None:
    """Register the session continuity tools."""
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def checkpoint(session_id: str, digest: str, title: str = "") -> str:
        """
        Persist a session snapshot BEFORE a context clear or compaction:
        the digest survives outside the context window and future
        sessions reload it through resume(). Idempotent per session_id
        (a later checkpoint for the same session supersedes the earlier
        one). Write anything the next session must not re-derive:
        decisions made, state of the work, open threads.
        """
        from codegraph.state.call_log import knowledge_list, knowledge_record

        previous = knowledge_list(
            tag="session-digest", session_id=session_id, limit=1, repo_root=_srv._root
        )
        entry_id = knowledge_record(
            title=title or f"Session digest {session_id}",
            body=digest,
            kind="note",
            tags="compaction,session-digest",
            session_id=session_id,
            repo_root=_srv._root,
            supersedes=previous[0]["id"] if previous else 0,
        )
        return json.dumps({"id": entry_id, "session_id": session_id})

    @mcp.tool()
    @_logged_tool
    def resume(
        session_id: str = "",
        task: str = "",
        budget_kb: float = 16,
        scope: str = "",
    ) -> str:
        """
        ONE call to rehydrate after a context clear: standing
        instructions first (never truncated), then recent session
        digests, task-relevant knowledge, open plans, and recent file
        summaries, ranked and capped at budget_kb. Pass the task for
        relevance ranking; pass scope="all" to also pull knowledge from
        federated subrepos (read-only, scope-tagged). Call this at
        session start when the header announces a bundle.
        """
        bundle = build_resume_bundle(
            _srv._root,
            session_id=session_id,
            task=task,
            budget_kb=budget_kb,
            scope=scope,
        )
        return json.dumps(bundle, indent=2)
