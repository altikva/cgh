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
            "command": "cgh index --root . 2>/dev/null || true",
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

- `context_for_task(task)`: first call on a coding task: ranked code, notes and plans
- `symbol_lookup(name)`: where is X defined (instead of grep/find)
- `search_symbols(query)`: a name you only half know
- `fts_search(query)`: code described in words; a sentence is fine
- `pattern_search(pattern, glob?)`: every place a string or regex occurs (instead of grep)
- `find_callers(fn_name) / find_callees(fn_name, max_depth?)`: who calls X / what X calls
- `file_summary(file_path)`: what a file holds, before reading it
- `impact_of(symbol_or_file) / tests_for(symbol_or_file)`: what depends on X / which tests cover it
- `search_docs(query) / doc_outline(file_path)`: Markdown docs
- `knowledge_search(query)`: notes saved in earlier sessions, not the code
- `scan_status(), then incremental_reindex()`: after a pull or branch switch, if stale

The full list, one question per tool, is in docs/MCP_TOOLS.md of the cgh repository.

## Best practices

1. Use `context_for_task` FIRST before reading files: saves 60-90% tokens
2. Use `symbol_lookup` instead of grepping for definitions
3. Use `find_callers`/`find_callees` instead of manual code navigation
4. Use `search_docs` to find relevant documentation before diving into code
