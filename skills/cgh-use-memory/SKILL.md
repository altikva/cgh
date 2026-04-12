---
name: cgh-use-memory
description: Check codegraph's memory + plan indexes BEFORE asking the user about preferences, before re-deriving facts from conversation history, and when the user hints at a past plan. Uses mcp__codegraph__memory_search, memory_list, plan_search, plan_list.
---

# Use codegraph memory + plan indexes

Claude Code's auto-memory and plan-mode files are now indexed by
codegraph. Query them **before** asking the user to repeat themselves
or before ignoring context you already have access to.

## Triggers → tool

| Situation | Tool to call first |
|---|---|
| User is about to do something that may have a stored preference (commit, PR, code style, naming) | `mcp__codegraph__memory_search(query="<topic>", kind="feedback")` |
| User mentions a past plan | `mcp__codegraph__plan_search(query="<keywords>")` |
| Facing a problem that might be solved before (gotchas, patterns) | `mcp__codegraph__knowledge_search(query="<keywords>")` |
| Unsure what topics are already captured | `mcp__codegraph__knowledge_terms()` (glossary) |
| User asks *"what do you know about this project"* | `mcp__codegraph__memory_list(kind="project")` + `knowledge_list()` |
| Any non-trivial task | `mcp__codegraph__context_for_task` — merges memory + plan + knowledge |

## When to RECORD knowledge (not just read it)

Call `mcp__codegraph__knowledge_record` when you notice:
- A **pattern** the codebase follows (*"all handlers return HTTPException on 404"*)
- A **decision** with rationale (*"chose Stripe over PayPal because SEPA coverage"*)
- A **gotcha** worth saving (*"Kuzu holds the lock until Python GC runs"*)
- A **style** preference (*"user prefers French in commit bodies"*)
- A **glossary** term (*"RFM = Recency, Frequency, Monetary"*)

Use `kind` to classify and `tags` to make it discoverable (tags form the
glossary via `knowledge_terms`).

## Context compaction

Before a long session gets compacted/summarized, call:

  `mcp__codegraph__compact_session(session_id, title, digest, tags=...)`

with a distilled summary of what was learned. It persists across
sessions — a future `context_for_task` call with relevant keywords
will surface it automatically.

## `context_for_task` now returns memory + plans

The unified `context_for_task` tool surfaces:
- top code symbols (as before),
- top 3 Claude Code memory entries matching the task,
- top 2 related plan files.

Prefer this for task kickoff — one call gives the full picture.

## Session dedup (optional, recommended)

When you make multiple `context_for_task` calls in the same session,
pass `session_id=<stable-string>` (any id stable for the current user
session). Codegraph hides already-shown nodes on subsequent calls so
you don't re-read the same symbols/memories/plans.

Reset when starting a fresh sub-task:
`mcp__codegraph__session_reset(session_id=<id>)`.

## Don't

- Don't ask the user to restate a preference you could have looked up
  via `memory_search`.
- Don't re-explain architectural choices on repeat topics — check
  `memory_list(kind="project")` first.
- Don't create a new plan when an equivalent one exists — `plan_search`
  first, reuse if relevant.
- Don't pass raw file contents to `memory_search`/`plan_search` — they
  expect a natural-language or keyword query.

## Staleness

Memory/plan indexes auto-refresh via the watcher (~1s after a file
save). If in doubt, call `mcp__codegraph__memory_rescan` or
`mcp__codegraph__plan_rescan` — both cheap, mtime-idempotent.
