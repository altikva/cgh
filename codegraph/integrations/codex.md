# cgh for OpenAI Codex CLI

## Setup

Codex CLI supports MCP servers. Add to your project config:

```bash
# Initialize cgh in your project
cgh init
cgh index
```

Then configure Codex to use cgh as MCP server:

```json
{
  "mcpServers": {
    "cgh": {
      "command": "cgh",
      "args": ["serve", "--root", ".", "--watch", "--reindex"]
    }
  }
}
```

## AGENTS.md instructions

Add to `AGENTS.md` to teach Codex to use cgh:

```markdown
## Code Navigation

This project uses cgh for code indexing. Use MCP tools to navigate:
- symbol_lookup(name): find symbol definitions (use instead of grep/find)
- context_for_task(description): get ranked context before starting any task
- search_docs(query): search project documentation
- find_callers(fn) / find_callees(fn): understand call relationships
- scan_repo(): refresh index after major changes

Always use cgh tools before reading files to minimize token usage.
```

## Environment variable

```bash
export CODEGRAPH_ROOT=/path/to/project
```
