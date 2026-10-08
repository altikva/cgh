# codegraph: AI Agent Instructions

> Copy the section below into your CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
> or any AI instruction file to teach your AI assistant to use codegraph.

---

## Code Navigation (codegraph)

This project is indexed by codegraph: a local code graph with symbol lookup,
call graphs, doc search, and BM25 full-text search exposed via MCP tools.

### Rules

1. **Use `context_for_task(task)` FIRST** before any coding task: it returns
   ranked symbols + docs + relationships in one call, saving 60-90% of tokens.
2. **Use `symbol_lookup(name)` instead of grep/find**: returns exact file:line.
3. **Use `search_docs(query)` before reading documentation files.**
4. **Use `find_callers`/`find_callees` instead of manual code navigation.**
5. **Only read the specific lines returned by codegraph**, not entire files.
6. **After branch switches, rebases or large pulls, call `scan_status()`**, then
   `incremental_reindex()` if it says stale.
7. **`knowledge_search` searches saved notes, not the code**: an empty result
   says nothing about what the code contains.

### Available MCP Tools

| Tool | Question it answers |
|------|---------------------|
| `context_for_task(task)` | First call on a coding task: ranked code, notes and plans |
| `symbol_lookup(name)` | Where is X defined (instead of grep/find) |
| `search_symbols(query)` | A name you only half know |
| `fts_search(query)` | Code described in words; a sentence is fine |
| `pattern_search(pattern, glob?)` | Every place a string or regex occurs (instead of grep) |
| `find_callers(fn_name) / find_callees(fn_name, max_depth?)` | Who calls X / what X calls |
| `file_summary(file_path)` | What a file holds, before reading it |
| `impact_of(symbol_or_file) / tests_for(symbol_or_file)` | What depends on X / which tests cover it |
| `search_docs(query) / doc_outline(file_path)` | Markdown docs |
| `knowledge_search(query)` | Notes saved in earlier sessions, not the code |
| `scan_status(), then incremental_reindex()` | After a pull or branch switch, if stale |

The full list, one question per tool, is in docs/MCP_TOOLS.md of the cgh repository.

---
