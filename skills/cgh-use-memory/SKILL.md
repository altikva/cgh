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
| User mentions a past plan (*"the refactor we planned"*, *"my last codegraph plan"*) | `mcp__codegraph__plan_search(query="<keywords>")` |
| User asks *"what do you know about this project"* | `mcp__codegraph__memory_list(kind="project")` |
| Any non-trivial task | `mcp__codegraph__context_for_task` — already merges memory + plan hits |

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
