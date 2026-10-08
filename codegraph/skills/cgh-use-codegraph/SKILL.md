---
name: cgh-use-codegraph
description: Use codegraph MCP tools BEFORE reading files when navigating code. Triggers on symbol lookups ("where is X defined", "what calls Y"), multi-file exploration ("how does X work"), and task kickoff. Saves 60-90% of exploration tokens.
---

# Use codegraph before reading files

Codegraph is a local code graph (symbols, calls, imports, docs) exposed over
MCP. Using it for navigation returns exact file paths + line ranges so you
only read the lines you actually need.

**Token economics**: MCP tool execution runs server-side and costs zero
model tokens. The only token cost is the JSON response, which is capped
and truncated. Read/Grep, in contrast, pulls the entire file into the
transcript. Call codegraph tools liberally: they're almost always
cheaper than reading. Default bias: call the tool, don't hesitate.

## Triggers → tool

| Situation | Tool to call first |
|---|---|
| "Where is `X` defined?" | `mcp__codegraph__symbol_lookup(name="X")` |
| "What calls `fn`?" / "Who uses `fn`?" | `mcp__codegraph__find_callers(fn_name="fn")` |
| "What does `fn` call?" | `mcp__codegraph__find_callees(fn_name="fn")` |
| "Trace the flow from `fn`" (multi-hop) | `mcp__codegraph__find_callees(fn_name="fn", max_depth=N)`: one call returns the ordered chain, don't chain single hops |
| "What breaks if I change `X`?" | `mcp__codegraph__impact_of(symbol_or_file="X", max_depth=N)` |
| "Which tests should I run?" | `mcp__codegraph__tests_for(symbol_or_file="X")`, or `impact_report(changed_files=[...])` for a change set |
| Fuzzy / partial name | `mcp__codegraph__search_symbols(query="...")` |
| Code described in words (a sentence is fine) | `mcp__codegraph__fts_search(query="...")` |
| "What is in `file.py`?" before reading it | `mcp__codegraph__file_summary(file_path="...")` |
| **Regex / substring over files** | `mcp__codegraph__pattern_search(pattern, glob?)`: INSTEAD of Grep |
| "How does [feature] work?": starting a non-trivial task | `mcp__codegraph__context_for_task(task="...")` |
| "Which doc covers this?" / "What docs mention `X`?" | `mcp__codegraph__search_docs` / `doc_refs` |
| "What does `file.py` import, who imports it?" | `mcp__codegraph__subgraph(file_path="...")` |
| "Is `fn` dead / unused?" | `mcp__codegraph__find_dead_code` (per-scope candidates, not a verdict) |

`knowledge_search` searches notes saved in earlier sessions, not the code:
an empty result there says nothing about what the code contains.

## Workflow

1. Identify the target (symbol name, file, or task description).
2. Call the relevant codegraph tool.
3. Use the returned `file_path` + `start_line`/`end_line` to `Read` only the
   needed range: not the whole file.

## Don't

- Don't Grep / Read a file "to find where X is" if `symbol_lookup` would
  return it directly.
- Don't route the same search through Bash: `git grep`, `grep -r`, `rg`,
  `find -name`, or `sed -n` / `cat` on a source file are Grep and Read by
  another path. Shell search is for logs and files cgh does not index.
- Don't read an entire file just to list its functions: use `file_summary`
  (or `doc_outline` for markdown).
- Don't run manual `cgh` CLI commands during a session; use MCP tools so
  results are logged and cached.

## Staleness

The graph reflects the last scan, not the live working tree. If the user
mentions recent `git pull`, `checkout`, `rebase`, or major edits, call
`mcp__codegraph__scan_status` first. If `fresh=false`, call
`mcp__codegraph__incremental_reindex` before trusting symbol results
(`scan_repo` is the slow full rebuild, a last resort).
