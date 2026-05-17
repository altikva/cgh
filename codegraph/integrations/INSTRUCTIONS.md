# codegraph — AI Agent Instructions

> Copy the section below into your CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
> or any AI instruction file to teach your AI assistant to use codegraph.

---

## Code Navigation (codegraph)

This project is indexed by codegraph — a local code graph with symbol lookup,
call graphs, doc search, and BM25 full-text search exposed via MCP tools.

### Rules

1. **Use `context_for_task(task)` FIRST** before any coding task — it returns
   ranked symbols + docs + relationships in one call, saving 60-90% of tokens.
2. **Use `symbol_lookup(name)` instead of grep/find** — returns exact file:line.
3. **Use `search_docs(query)` before reading documentation files.**
4. **Use `find_callers`/`find_callees` instead of manual code navigation.**
5. **Only read the specific lines returned by codegraph**, not entire files.
6. **Call `scan_repo()` after branch switches, rebases, or large pulls.**

### Available MCP Tools

| Tool | Usage |
|------|-------|
| `context_for_task(task)` | Ranked context for a natural language task |
| `symbol_lookup(name)` | Find where a symbol is defined |
| `search_symbols(query)` | Fuzzy search across functions, classes, docs |
| `search_docs(query)` | Search Markdown documentation |
| `find_callers(fn_name)` | Who calls this function? |
| `find_callees(fn_name)` | What does this function call? |
| `doc_outline(file_path)` | Table of contents for a Markdown file |
| `doc_refs(symbol_name)` | Find docs that reference a symbol |
| `fts_search(query, kind?)` | BM25 full-text search (names + docstrings) |
| `visualize_graph(scope)` | Mermaid/DOT diagrams |
| `scan_repo()` | Full re-index |
| `index_changed_files(since?)` | Incremental index since git ref |
| `force_index(paths, confirmed)` | Index ignored files (requires confirmation) |
| `graph_stats()` | Node and edge counts |
| `call_stats()` | MCP tool usage statistics |

---
