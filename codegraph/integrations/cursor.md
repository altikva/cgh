# cgh for Cursor

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

Add to `.cursorrules` to teach Cursor to use cgh:

```
When navigating code or answering questions about this codebase:
1. Use cgh MCP tools BEFORE reading files
2. Call symbol_lookup(name) to find where a symbol is defined instead of searching manually
3. Call context_for_task(description) at the start of any task for ranked context
4. Call search_docs(query) to find relevant documentation
5. Call find_callers/find_callees to understand call relationships
6. Only read the specific lines returned by cgh, not entire files

Available cgh tools: symbol_lookup, search_symbols, search_docs, find_callers,
find_callees, context_for_task, doc_outline, doc_refs, fts_search, visualize_graph,
scan_repo, index_changed_files, graph_stats
```

## Environment variable

Override cgh location:
```bash
export CODEGRAPH_ROOT=/path/to/project
```
