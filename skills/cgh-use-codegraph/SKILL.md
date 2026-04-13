---
name: cgh-use-codegraph
description: Use codegraph MCP tools BEFORE reading files when navigating code. Triggers on symbol lookups ("where is X defined", "what calls Y"), multi-file exploration ("how does X work"), and task kickoff. Saves 60-90% of exploration tokens.
---

# Use codegraph before reading files

Codegraph is a local code graph (symbols, calls, imports, docs) exposed over
MCP. Using it for navigation returns exact file paths + line ranges so you
only read the lines you actually need.

## Triggers → tool

| Situation | Tool to call first |
|---|---|
| "Where is `X` defined?" | `mcp__codegraph__symbol_lookup(name="X")` |
| "What calls `fn`?" / "Who uses `fn`?" | `mcp__codegraph__find_callers(fn_name="fn")` |
| "What does `fn` call?" | `mcp__codegraph__find_callees(fn_name="fn")` |
| Fuzzy / partial name | `mcp__codegraph__search_symbols(query="...")` |
| Full-text (docstrings, names) | `mcp__codegraph__fts_search(query="...")` |
| **Regex / substring over files** | `mcp__codegraph__pattern_search(pattern, glob?)` — INSTEAD of Grep |
| "How does [feature] work?" — starting a non-trivial task | `mcp__codegraph__context_for_task(task="...")` |
| "What docs mention `X`?" | `mcp__codegraph__search_docs` / `doc_refs` |
| "Blast radius of `file.py`" | `mcp__codegraph__subgraph(file_path="...")` |
| "Is `fn` dead / unused?" | `mcp__codegraph__find_dead_code` |

## Workflow

1. Identify the target (symbol name, file, or task description).
2. Call the relevant codegraph tool.
3. Use the returned `file_path` + `start_line`/`end_line` to `Read` only the
   needed range — not the whole file.

## Don't

- Don't Grep / Read a file "to find where X is" if `symbol_lookup` would
  return it directly.
- Don't read an entire file just to list its functions — use `doc_outline`
  for markdown or walk symbols via `search_symbols`.
- Don't run manual `cgh` CLI commands during a session; use MCP tools so
  results are logged and cached.

## Staleness

The graph reflects the last scan, not the live working tree. If the user
mentions recent `git pull`, `checkout`, `rebase`, or major edits, call
`mcp__codegraph__scan_status` first. If `fresh=false`, call
`mcp__codegraph__scan_repo` before trusting symbol results.
