# codegraph for OpenAI Codex CLI

## Setup

Codex CLI supports MCP servers. Add to your project config:

```bash
# Initialize codegraph in your project
cgh init
cgh index
```

Then configure Codex to use codegraph as MCP server:

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

## AGENTS.md instructions

Add to `AGENTS.md` to teach Codex to use codegraph:

```markdown
## Code Navigation

This project uses codegraph for code indexing. Use MCP tools to navigate:
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

Always use codegraph tools before reading files to minimize token usage.
```

## Environment variable

```bash
export CODEGRAPH_ROOT=/path/to/project
```
