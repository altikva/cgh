---
name: cgh-record-knowledge
description: Persist patterns, decisions, gotchas, style preferences, and session digests into codegraph's knowledge store so they survive across sessions and context compactions. Complements cgh-use-memory (which is about READING). Triggers proactively after an "aha" moment, after diagnosing a non-obvious bug, or near the end of a long session.
---

# Record knowledge: codegraph write side

Codegraph has a persistent knowledge store backed by SQLite FTS. Unlike
memory files (which the user curates via their own memory skill), the
knowledge store is for **insights you produce during a session** so they
survive context compaction and are surfaced automatically on future
`context_for_task` calls.

## When to record (be proactive)

Call `mcp__codegraph__knowledge_record` when you notice any of:

| Type | Example | `kind` |
|---|---|---|
| A recurring pattern in the codebase | "All handlers return HTTPException on 404" | `pattern` |
| A decision + its rationale | "Chose Stripe over PayPal because SEPA coverage" | `decision` |
| A non-obvious gotcha | "Kuzu holds the OS lock until Python GC runs" | `gotcha` |
| A user style preference | "User prefers French in commit bodies" | `style` |
| A glossary term | "RFM = Recency, Frequency, Monetary" | `glossary` |
| Any other reusable insight | "Watcher debounce is 300ms in codegraph" | `note` |

Do NOT record:
- Things that are already in CLAUDE.md / memory files: those are already
  surfaced via `memory_search`.
- Trivia only relevant to the current conversation turn.
- The user's questions themselves: only your answers + insights.

## How to record

```
knowledge_record(
    title="short, searchable",       # required
    body="2-5 sentences of detail",  # required
    kind="pattern|decision|gotcha|style|glossary|note",
    tags="comma,separated,short",    # these become the glossary index
    file_refs="path1,path2",         # canonical files this refers to
    session_id="<stable session id>" # optional but recommended
)
```

**Tag discipline**: tags ARE the glossary. Use 2-5 short lowercase tags
per entry. Reuse existing tags when possible: call `knowledge_terms()`
first to see what's already in use.

## Context lifecycle: persist before compaction, reload after

Your context window is finite. Knowledge survives compaction: raw
conversation does not. This is the most important workflow in codegraph.

### Before compaction (~80% context usage)

When you sense the window tightening (long session, many tool results,
system warns about approaching limits):

1. **Record everything non-trivial** you learned this session:
   ```
   knowledge_record(title=..., body=..., kind=..., tags=..., file_refs=...)
   ```
   Better to over-record than to lose. If in doubt, record it.

2. **Compact the session**:
   ```
   compact_session(
       session_id=<same id used during the session>,
       title="<what we worked on>",
       digest="<3-8 bullet points of what was learned / decided / shipped>",
       tags="<topics>,session-digest"
   )
   ```
   This persists a `kind=note` entry stamped with the session_id.

### After compaction / session resume

This is CRITICAL: without this step you restart from zero:

1. `knowledge_list(limit=20)`: reload recent learnings
2. `knowledge_search(query)`: targeted reload for the current task
3. `memory_search(query)`: reload user preferences and feedback
4. `plan_search(query)`: reload any active plan
5. `knowledge_terms()`: see the full glossary of captured topics

### Session start (new conversation)

Same reload sequence. Always start with:
```
context_for_task(task="<what the user wants>", session_id=<new_id>)
```
This automatically merges code graph + memory + plans + knowledge.
Then supplement with targeted `knowledge_search` if needed.

## Before recording: check for duplicates

1. `knowledge_search(query="<your planned title>")`
2. If a near-duplicate exists, consider `knowledge_forget(id)` then
   re-record with richer content, rather than creating a second entry.

## Don't

- Don't record the user's raw prompts.
- Don't record secrets, tokens, or PII (the DB sits in `.codegraph/`
  which is gitignored but users may inspect it).
- Don't create an entry per line of a decision: one per coherent insight.
- Don't skip `tags`: without them the glossary can't aggregate the topic.
