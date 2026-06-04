---
name: cgh-feature-plan
description: Plan a new feature or architectural change by FIRST mapping the codebase through codegraph MCP tools instead of Read/Grep. Triggers when the user describes what they want to build or changes ("I want X", "can we add Y", "how should we implement Z", "refactor X to do Y").
---

# Plan a feature through codegraph

When the user describes a new feature, a refactor, or any broad change,
resist the reflex to Read / Grep / ls files. Call codegraph MCP tools
first: they return exact file paths + line numbers, so the Read calls
that follow are surgical.

## Required sequence

1. **`mcp__codegraph__context_for_task(task, session_id=<id>)`**
   Pass the user's full description. Returns the most-relevant symbols
   with their docstrings and relationships (callers, callees, inheritors),
   PLUS top Claude Code memory entries and related plan files in one
   response. Pass a stable `session_id` so subsequent calls don't
   re-serve already-shown entities.
   If it returns empty code results, move on: don't block the plan.

2. **`mcp__codegraph__architecture_overview()`**
   Compact map of files grouped by layer + role (router / handler /
   manager / service / model / schema / component / store / ...). Use
   this to locate where the new feature should live.

3. **`mcp__codegraph__domain_map(keyword)`**
   For every noun the user mentions (e.g. "donors", "stats", "receipts"),
   call this to get every file touching that domain grouped by role.
   Do this in PARALLEL for multiple keywords.

4. **`mcp__codegraph__endpoints(path_pattern)`**
   If the feature involves HTTP (API calls, new routes, frontend/backend
   coupling), list existing endpoints matching the domain. Reveals the
   FastAPI ↔ Nuxt contract.

5. **`mcp__codegraph__memory_search(query, kind="feedback")`**: only when
   the user hints at preferences you might already know. Avoids asking
   the same question twice across sessions.

6. **`mcp__codegraph__plan_search(query)`**: when the user mentions a
   past plan ("the refactor we discussed"). Reuse rather than re-derive.

7. **`mcp__codegraph__find_callers(fn_name)` / `find_callees(fn_name)`**
   Only for symbols identified in steps 1-4 that you plan to change.
   Never blindly on arbitrary names.

## After the map is clear

6. **Summarise the touch points** to the user in 3-6 bullet points
   before writing any code. Format:
   ```
   • api/handlers/donation_handler.py:42-58: DonationHandler.list
   • api/routers/donations.py:87: GET /donations route
   • apps/admin/pages/donations.vue:12-40: frontend consumer
   ```
   Ask for confirmation on scope before implementing.

7. **Read only the exact line ranges** returned by the tools. Never Read
   full files during planning: only during implementation for the
   specific regions you'll modify.

## What NOT to do

- No `ls`, `find`, or `tree`: `architecture_overview` already gives you
  the structure.
- No `grep`/`rg` for symbol names: `search_symbols` / `symbol_lookup`
  are 100× more precise (match class/function nodes, not string
  occurrences).
- No Read of CLAUDE.md to find where handlers live: the role taxonomy
  in `architecture_overview` is canonical.
- No parallel speculative Reads. Plan first, then Read only what you'll
  edit.

## Token savings

A typical feature plan on this repo:
- Without codegraph:  ~8-12 tool calls (ls, Grep, Read × 4-6) →
  30k+ tokens just to locate files.
- With this skill:    3-5 tool calls, < 5k tokens, more accurate
  results.
