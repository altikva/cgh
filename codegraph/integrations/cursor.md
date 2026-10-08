# codegraph for Cursor

## Setup (MCP server)

Add to `.cursor/mcp.json` at your repo root:

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "codegraph",
      "args": ["serve", "--root", ".", "--watch", "--reindex"]
    }
  }
}
```

## Cursor Rules (.cursorrules)

Add to `.cursorrules` to teach Cursor to use codegraph:

```
When navigating code or answering questions about this codebase:
1. Use codegraph MCP tools BEFORE reading files
2. Call symbol_lookup(name) to find where a symbol is defined instead of searching manually
3. Call context_for_task(description) at the start of any task for ranked context
4. Call search_docs(query) to find relevant documentation
5. Call find_callers/find_callees to understand call relationships
6. Only read the specific lines returned by codegraph, not entire files

Pick the codegraph tool by the question:
- context_for_task(task): first call on a coding task: ranked code, notes and plans
- symbol_lookup(name): where is X defined (instead of grep/find)
- search_symbols(query): a name you only half know
- fts_search(query): code described in words; a sentence is fine
- pattern_search(pattern, glob?): every place a string or regex occurs (instead of grep)
- find_callers(fn_name) / find_callees(fn_name, max_depth?): who calls X / what X calls
- file_summary(file_path): what a file holds, before reading it
- impact_of(symbol_or_file) / tests_for(symbol_or_file): what depends on X / which tests cover it
- search_docs(query) / doc_outline(file_path): Markdown docs
- knowledge_search(query): notes saved in earlier sessions, not the code
- scan_status(), then incremental_reindex(): after a pull or branch switch, if stale
```

## Environment variable

Override codegraph location:
```bash
export CODEGRAPH_ROOT=/path/to/project
```
