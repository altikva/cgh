# codegraph for Claude Code

## Setup (MCP server)

Add to `.mcp.json` at your repo root:

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

If codegraph is installed in a venv:
```json
{
  "mcpServers": {
    "codegraph": {
      "command": "/path/to/venv/bin/codegraph",
      "args": ["serve", "--root", "/path/to/repo", "--watch", "--reindex"]
    }
  }
}
```

## Hooks (settings.json)

Add to `.claude/settings.json` for auto-indexing on commit:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash(git commit*)",
        "hooks": [
          {
            "type": "command",
            "command": "codegraph index --root . 2>/dev/null || true",
            "async": true,
            "statusMessage": "codegraph: indexing changes"
          }
        ]
      }
    ]
  }
}
```

## Available MCP tools

Once connected, Claude Code can use:

- `symbol_lookup(name)`: find where a symbol is defined
- `search_symbols(query)`: fuzzy search across all symbols
- `search_docs(query)`: search markdown documentation
- `find_callers(fn_name)`: who calls this function?
- `find_callees(fn_name)`: what does this function call?
- `context_for_task(task)`: build ranked context for any task
- `doc_outline(file)`: markdown heading tree
- `doc_refs(symbol)`: find docs referencing a symbol
- `fts_search(query)`: BM25 full-text search
- `visualize_graph(scope)`: generate Mermaid diagrams
- `scan_repo()`: full re-index
- `index_changed_files(since)`: incremental index
- `force_index(paths, confirmed)`: bypass .gitignore
- `graph_stats()`: node/edge counts
- `call_stats()`: tool usage statistics

## Best practices

1. Use `context_for_task` FIRST before reading files: saves 60-90% tokens
2. Use `symbol_lookup` instead of grepping for definitions
3. Use `find_callers`/`find_callees` instead of manual code navigation
4. Use `search_docs` to find relevant documentation before diving into code
