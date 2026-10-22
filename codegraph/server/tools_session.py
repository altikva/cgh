# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Session continuity in one call each. checkpoint persists a
#              session digest before a clear or compaction; resume answers in
#              two levels. By default a summary of about 2 KB: the standing
#              instructions (short bodies in full, long ones clipped and
#              flagged), the last session's goal and next step, the known dead
#              ends and the counts of what is stored. On demand, detail=True
#              returns the whole ranked, budgeted bundle (standing
#              instructions, recent digests, known dead ends, task-relevant
#              knowledge, open plans, recent file summaries), and section=
#              returns one of its parts.

from __future__ import annotations

import json
import re

# The share of the post-instructions budget reserved for the knowledge bucket,
# so earlier sections (digests, and especially contentless auto checkpoints)
# cannot starve it. Without this a long standing instruction plus a few digests
# emptied knowledge and the only signal was truncated:true.
_KNOWLEDGE_RESERVE = 0.4

# How many knowledge entries the bucket aims to carry.
_KNOWLEDGE_LIMIT = 8

# How many known dead ends (entries and files whose recorded outcomes say they
# did not work) the bundle carries, strongest and most recent first.
_DEAD_ENDS_LIMIT = 5

# The parts resume(section=...) returns, by the name the caller passes. The
# bundle's own key names are accepted too.
SECTIONS = {
    "instructions": "standing_instructions",
    "digests": "digests",
    "dead_ends": "known_dead_ends",
    "knowledge": "knowledge",
    "plans": "open_plans",
    "summaries": "recent_summaries",
    "federated": "federated_knowledge",
}

# The summary's target size, in bytes of compact JSON. Every byte of a resume
# answer is re-read on each later turn of the session; the whole bundle
# measured 8 KB per call on real sessions.
SUMMARY_BUDGET = 2048

# A standing instruction body up to this length rides in full in the summary.
# A longer one is cut to its lead and flagged, and the summary says to read
# it in full before acting on it.
_SHORT_BODY = 280
_BODY_LEAD = 200
_TITLE_MAX = 120

# The checkpoint labels the summary reads the goal and the next step from,
# in the order the cgh-checkpoint skill writes them, English or French.
# A label ends its line or is followed by a colon or a dash, so "Next, the
# portal" or "But it failed" in prose is not read as one.
_END = r"\s*(?:[:\-\u2013]|$)"
_GOAL_RE = re.compile(r"^(?:goal|objectif|but)" + _END, re.IGNORECASE)
_NEXT_RE = re.compile(
    r"^(?:next(?:\s+steps?)?|prochaines?\s+[ée]tapes?|suite)" + _END,
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"^(?:goal|objectif|but|done|decisions?|open|next(?:\s+steps?)?|"
    r"prochaines?\s+[ée]tapes?|suite|fait|d[ée]cisions?|ouvert)" + _END,
    re.IGNORECASE,
)


def _is_auto_checkpoint(entry: dict) -> bool:
    """A contentless automatic SessionEnd/PreCompact marker (tagged
    auto-checkpoint), as opposed to a model-written digest. These carry no
    recoverable content, so they rank below real digests."""
    return "auto-checkpoint" in (entry.get("tags") or "")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _strip_label_line(line: str) -> str:
    """A digest line without its list number, bullets and bold markers, so
    "1. **Goal**: x" and "## Next step" read as "Goal: x" and "Next step"."""
    line = line.strip()
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"^(?:\d+[.)]|[-*+])\s+", "", line)
    return line.replace("**", "").replace("__", "").strip()


def digest_brief(body: str, limit: int = 300) -> dict:
    """The goal and the next step of a checkpoint, read from its labels
    ("Goal:", "Next step:", a "NEXT" heading, their French forms). A label
    alone on its line takes the lines below it, up to the next label or a
    blank line. Missing labels are left out; with neither, an excerpt of
    the body stands in."""
    lines = str(body or "").splitlines()
    found: dict[str, str] = {}
    for i, raw in enumerate(lines):
        line = _strip_label_line(raw)
        for key, pattern in (("goal", _GOAL_RE), ("next", _NEXT_RE)):
            if key in found or not pattern.match(line):
                continue
            rest = re.sub(r"^[^:\-\u2013]*?[:\-\u2013]\s*", "", line, count=1)
            text = rest if rest != line else ""
            if not text:
                tail = []
                for nxt in lines[i + 1 :]:
                    clean = _strip_label_line(nxt)
                    if not clean or _LABEL_RE.match(clean):
                        break
                    tail.append(clean)
                text = " ".join(tail)
            if text:
                found[key] = _clip(text, limit)
    if not found:
        found["excerpt"] = _clip(body, limit)
    return found


def _digests(repo_root, session_id: str = "") -> list[dict]:
    """The digests a resume carries: the caller's session first, content
    bearing ones before at most one contentless auto marker."""
    from codegraph.state.call_log import knowledge_list

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

    # Rescue digests written before they carried the session-digest tag:
    # compact_session once stored the digest as a plain note whose only marker
    # was a non-empty session_id, so it never matched the tagged query above and
    # fell silently into the knowledge bucket while the contentless auto marker
    # took the digest slot, which reads exactly like lost memory. A note that
    # carries a session_id and is not an auto marker is a model-written digest;
    # fold it in so digests already on disk are recovered, not only new ones.
    # Only reach for this when the tagged path found nothing, to stay a no-op on
    # stores that already have real tagged digests.
    if not content_digests:
        known = {d["id"] for d in raw_digests}
        if session_id:
            candidates = knowledge_list(
                kind="note", session_id=session_id, limit=3, repo_root=repo_root
            )
        else:
            candidates = [
                d
                for d in knowledge_list(kind="note", limit=20, repo_root=repo_root)
                if d.get("session_id")
            ]
        content_digests = [
            d for d in candidates if d["id"] not in known and not _is_auto_checkpoint(d)
        ][:3]

    return content_digests + auto_digests[:1]


def _collect_sections(
    repo_root, session_id: str = "", task: str = "", scope: str = ""
) -> list[tuple[str, list[dict]]]:
    """Every section of the bundle, uncapped, in priority order."""
    from codegraph.core.fts import get_fts_conn, list_plan_entries
    from codegraph.state.call_log import (
        knowledge_dead_ends,
        knowledge_list,
        knowledge_search,
    )
    from codegraph.state.findings import query_findings

    sections: list[tuple[str, list[dict]]] = []

    instructions = knowledge_list(
        kind="standing_instruction", limit=20, repo_root=repo_root
    )
    sections.append(("standing_instructions", instructions))

    digests = _digests(repo_root, session_id)
    sections.append(("digests", digests))

    # Paths an earlier session already found to go nowhere, so this one does
    # not retry them. Small and capped, right after the digests.
    sections.append(
        ("known_dead_ends", knowledge_dead_ends(_DEAD_ENDS_LIMIT, repo_root=repo_root))
    )

    # Coverage-first: a task ranks the entries (BM25 is strict AND over the
    # terms), but a search that matches nothing must never SHRINK the bucket,
    # so backfill with recent entries up to the limit. The no-task path is pure
    # backfill.
    #
    # The knowledge bucket is for real learnings (decisions, gotchas, patterns),
    # NOT session digests, which have their own bucket above. Exclude the
    # session-digest tag from both paths: without it, a long-lived store whose
    # recent rows are all auto-checkpoint markers buries every real entry, since
    # the recency backfill fills up on the markers and never reaches the older
    # decisions and gotchas. That is how a store with hundreds of checkpoints
    # returned zero knowledge on resume.
    knowledge = [
        k
        for k in (
            knowledge_search(task, limit=_KNOWLEDGE_LIMIT, repo_root=repo_root)
            if task
            else []
        )
        if "session-digest" not in (k.get("tags") or [])
    ]
    have = {k["id"] for k in knowledge}
    if len(knowledge) < _KNOWLEDGE_LIMIT:
        for k in knowledge_list(
            limit=_KNOWLEDGE_LIMIT,
            exclude_tag="session-digest",
            repo_root=repo_root,
            rank_outcomes=True,
        ):
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
    return sections


def _size(entry) -> int:
    return len(json.dumps(entry, default=str))


def _fit_bundle(sections: list[tuple[str, list[dict]]], budget_kb: float) -> dict:
    """Cap the sections at budget_kb.

    Standing instructions are never dropped. Knowledge gets a reserved share
    of what's left so digests (and contentless checkpoints) cannot starve it,
    the reported failure where knowledge came back empty with only
    truncated:true as a signal. Every section that loses entries to the
    budget records the count under dropped_for_budget, so an empty section is
    never silently read as lost memory."""
    budget = int(budget_kb * 1024)
    by_name = dict(sections)
    bundle: dict = {"truncated": False, "dropped_for_budget": {}}

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
    for name in (
        "digests",
        "known_dead_ends",
        "open_plans",
        "recent_summaries",
        "federated_knowledge",
    ):
        if name in by_name:
            used = _fill(name, by_name[name], other_cap, used)

    # Knowledge gets its reserved floor plus whatever the others left unspent.
    _fill("knowledge", by_name.get("knowledge", []), budget, used)

    return bundle


def build_resume_bundle(
    repo_root,
    session_id: str = "",
    task: str = "",
    budget_kb: float = 16,
    scope: str = "",
) -> dict:
    """The whole bundle, priority-ordered and budget-capped: what
    resume(detail=True) returns."""
    return _fit_bundle(_collect_sections(repo_root, session_id, task, scope), budget_kb)


def resume_section(
    repo_root,
    section: str,
    session_id: str = "",
    task: str = "",
    budget_kb: float = 16,
    scope: str = "",
) -> dict:
    """One part of the bundle, with the whole budget to itself. Standing
    instructions come back in full whatever the budget. An unknown name
    answers with the list of valid ones."""
    key = SECTIONS.get(section, section)
    if key not in SECTIONS.values():
        return {
            "error": f"unknown section {section!r}",
            "sections": sorted(SECTIONS),
        }
    if key == "federated_knowledge":
        scope = "all"
    by_name = dict(_collect_sections(repo_root, session_id, task, scope))
    entries = by_name.get(key, [])
    out: dict = {"section": section, key: entries, "truncated": False}
    if key == "standing_instructions":
        return out
    budget, used, kept = int(budget_kb * 1024), 0, []
    for entry in entries:
        used += _size(entry)
        if used > budget:
            break
        kept.append(entry)
    out[key] = kept
    if len(kept) < len(entries):
        out["truncated"] = True
        out["dropped_for_budget"] = len(entries) - len(kept)
    return out


def resume_counts(repo_root) -> dict:
    """What the store holds, for the summary and the SessionStart header."""
    from codegraph.core.fts import get_fts_conn, list_plan_entries
    from codegraph.state.call_log import knowledge_count

    instructions = knowledge_count(kind="standing_instruction", repo_root=repo_root)
    digests = knowledge_count(tag="session-digest", repo_root=repo_root)
    total = knowledge_count(repo_root=repo_root)
    try:
        plans = len(list_plan_entries(get_fts_conn(repo_root), limit=1000))
    except Exception:
        plans = 0
    return {
        "standing_instructions": instructions,
        "digests": digests,
        "knowledge": max(0, total - digests - instructions),
        "plans": plans,
    }


def _instruction_line(entry: dict, lead: int | None) -> dict:
    """A standing instruction for the summary. lead None keeps the body in
    full; otherwise a long body is cut to lead characters (0 drops it) and
    flagged clipped."""
    out: dict = {"id": entry["id"], "title": _clip(entry["title"], _TITLE_MAX)}
    body = " ".join(str(entry.get("body") or "").split())
    if lead is None or len(body) <= _SHORT_BODY:
        if body:
            out["body"] = body
    else:
        if lead:
            out["body"] = _clip(body, lead)
        out["clipped"] = True
    return out


def resume_summary(repo_root, session_id: str = "", task: str = "") -> dict:
    """The default resume answer, about SUMMARY_BUDGET bytes: the standing
    instructions in a compact form, the last session's goal and next step,
    the known dead ends, the counts, and how to get the detail.

    Standing instructions ride in full while the summary fits the budget.
    Past it, a short body stays whole and a long one keeps its title and the
    lead of its body, flagged clipped, and the "more" line tells the agent to
    read those in full before acting on them, so no rule is silently lost.
    Titles are never dropped, even past the budget."""
    from codegraph.state.call_log import (
        knowledge_dead_ends,
        knowledge_list,
        knowledge_search,
    )

    instructions = knowledge_list(
        kind="standing_instruction", limit=20, repo_root=repo_root
    )
    digests = [d for d in _digests(repo_root, session_id) if not _is_auto_checkpoint(d)]
    last: dict = {}
    if digests:
        d = digests[0]
        last = {
            "id": d["id"],
            "session_id": d.get("session_id") or "",
            "title": _clip(d["title"], _TITLE_MAX),
            **digest_brief(d["body"]),
        }
        if session_id:
            last["own"] = (d.get("session_id") or "") == session_id

    dead_ends = [
        _clip(item.get("title") or item.get("file") or "", 100)
        for item in knowledge_dead_ends(_DEAD_ENDS_LIMIT, repo_root=repo_root)
    ]
    matching = []
    if task:
        matching = [
            {"id": k["id"], "title": _clip(k["title"], 100)}
            for k in knowledge_search(task, limit=3, repo_root=repo_root)
            if "session-digest" not in (k.get("tags") or [])
            and k.get("kind") != "standing_instruction"
        ]

    def compose(lead: int | None, brief: int) -> dict:
        lines = [_instruction_line(e, lead) for e in instructions]
        clipped = sum(1 for line in lines if line.get("clipped"))
        more = (
            "Detail on demand: resume(detail=True) for everything, or "
            "resume(section=...) with one of: instructions, digests, "
            "dead_ends, knowledge, plans, summaries."
        )
        if clipped:
            more = (
                f"{clipped} standing instruction(s) clipped: read them with "
                "resume(section='instructions') before acting on them. " + more
            )
        out: dict = {"mode": "summary", "standing_instructions": lines}
        if last:
            out["last_session"] = {
                k: _clip(v, brief) if k in ("goal", "next", "excerpt") else v
                for k, v in last.items()
            }
        if dead_ends:
            out["known_dead_ends"] = dead_ends
        if matching:
            out["matching_knowledge"] = matching
        out["counts"] = {
            **resume_counts(repo_root),
            "known_dead_ends": len(dead_ends),
        }
        out["more"] = more
        return out

    # Shrink in steps until the summary fits: every body in full when there
    # is room, then leads of the long ones, a shorter goal and next step, and
    # last titles alone for the long instructions.
    summary: dict = {}
    steps: tuple = ((None, 300), (_BODY_LEAD, 300), (120, 200), (0, 160))
    for lead, brief in steps:
        summary = compose(lead, brief)
        if _size(summary) <= SUMMARY_BUDGET:
            break
    return summary


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
        full: bool = False,
        detail: bool = False,
        section: str = "",
    ) -> str:
        """
        ONE call to rehydrate after a context clear. By default a short
        summary (about 2 KB): the standing instructions (follow them;
        any marked clipped must be read in full before acting), the last
        session's goal and next step (your own checkpoint first when you
        pass session_id), known dead ends (do not retry them), and the
        counts of what is stored. Pass the task to list matching
        knowledge titles.

        Detail on demand: detail=True (or full=True) returns the whole
        bundle, ranked and capped at budget_kb: standing instructions,
        recent digests, dead ends, task-relevant knowledge, open plans
        and file summaries. section= returns one part: instructions,
        digests, dead_ends, knowledge, plans, summaries or federated.
        scope="all" also pulls knowledge from federated subrepos and
        returns the whole bundle.
        """
        if section:
            out = resume_section(
                _srv._root,
                section,
                session_id=session_id,
                task=task,
                budget_kb=budget_kb,
                scope=scope,
            )
            return json.dumps(out, indent=2, default=str)
        if detail or full or scope == "all":
            bundle = build_resume_bundle(
                _srv._root,
                session_id=session_id,
                task=task,
                budget_kb=budget_kb,
                scope=scope,
            )
            return json.dumps(bundle, indent=2, default=str)
        summary = resume_summary(_srv._root, session_id=session_id, task=task)
        return json.dumps(summary, ensure_ascii=False, default=str)
