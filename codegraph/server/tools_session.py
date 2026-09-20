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

# The share of the post-instructions budget reserved for the knowledge bucket,
# so earlier sections (digests, and especially contentless auto checkpoints)
# cannot starve it. Without this a long standing instruction plus a few digests
# emptied knowledge and the only signal was truncated:true.
_KNOWLEDGE_RESERVE = 0.4

# How many knowledge entries the bucket aims to carry.
_KNOWLEDGE_LIMIT = 8


def _is_auto_checkpoint(entry: dict) -> bool:
    """A contentless automatic SessionEnd/PreCompact marker (tagged
    auto-checkpoint), as opposed to a model-written digest. These carry no
    recoverable content, so they rank below real digests."""
    return "auto-checkpoint" in (entry.get("tags") or "")


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

    raw_digests = []
    if session_id:
        raw_digests = knowledge_list(
            tag="session-digest", session_id=session_id, limit=3, repo_root=repo_root
        )
    raw_digests += [
        d
        for d in knowledge_list(tag="session-digest", limit=6, repo_root=repo_root)
        if d["id"] not in {x["id"] for x in raw_digests}
    ]
    # Content-bearing digests first; keep at most one contentless auto marker
    # (a "a session ended here" signal) and only after the real digests, so
    # empty checkpoints stop eating the budget ahead of knowledge.
    content_digests = [d for d in raw_digests if not _is_auto_checkpoint(d)]
    auto_digests = [d for d in raw_digests if _is_auto_checkpoint(d)]
    digests = content_digests + auto_digests[:1]
    sections.append(("digests", digests))

    # Coverage-first: a task ranks the entries (BM25 is strict AND over the
    # terms), but a search that matches nothing must never SHRINK the bucket,
    # so backfill with recent entries up to the limit. The no-task path is pure
    # backfill.
    knowledge = (
        knowledge_search(task, limit=_KNOWLEDGE_LIMIT, repo_root=repo_root)
        if task
        else []
    )
    have = {k["id"] for k in knowledge}
    if len(knowledge) < _KNOWLEDGE_LIMIT:
        for k in knowledge_list(limit=_KNOWLEDGE_LIMIT, repo_root=repo_root):
            if len(knowledge) >= _KNOWLEDGE_LIMIT:
                break
            if k["id"] not in have:
                knowledge.append(k)
                have.add(k["id"])
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

    # Budget. Standing instructions are never dropped. Knowledge gets a
    # reserved share of what's left so digests (and contentless checkpoints)
    # cannot starve it, the reported failure where knowledge came back empty
    # with only truncated:true as a signal. Every section that loses entries to
    # the budget records the count under dropped_for_budget, so an empty section
    # is never silently read as lost memory.
    budget = int(budget_kb * 1024)
    by_name = dict(sections)
    bundle: dict = {"truncated": False, "dropped_for_budget": {}}

    def _size(entry) -> int:
        return len(json.dumps(entry, default=str))

    def _fill(name: str, entries: list, cap: int, used: int) -> int:
        kept: list = []
        for entry in entries:
            s = _size(entry)
            if used + s > cap:
                break
            kept.append(entry)
            used += s
        if len(kept) < len(entries):
            bundle["dropped_for_budget"][name] = len(entries) - len(kept)
            bundle["truncated"] = True
        bundle[name] = kept
        return used

    instr = by_name.get("standing_instructions", [])
    bundle["standing_instructions"] = instr
    used = sum(_size(e) for e in instr)

    avail = max(0, budget - used)
    # The non-knowledge sections share the budget MINUS the knowledge reserve,
    # in priority order, so they cannot eat into knowledge's floor.
    other_cap = used + int(avail * (1 - _KNOWLEDGE_RESERVE))
    for name in ("digests", "open_plans", "recent_summaries", "federated_knowledge"):
        if name in by_name:
            used = _fill(name, by_name[name], other_cap, used)

    # Knowledge gets its reserved floor plus whatever the others left unspent.
    _fill("knowledge", by_name.get("knowledge", []), budget, used)

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
