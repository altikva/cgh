# cgh for OpenAI Codex CLI

## Setup

Codex CLI supports MCP servers. Add to your project config:

```bash
# Initialize cgh in your project
cgh init
cgh index
```

`cgh init codex` writes the server into `.codex/config.toml` (project scope),
the file Codex CLI reads MCP servers from. The entry looks like:

```toml
[mcp_servers.cgh]
command = "cgh"
args = ["serve", "--root", ".", "--watch", "--reindex"]
```

Codex loads a project `.codex/config.toml` only in trusted projects; for a
global install put the same `[mcp_servers.cgh]` table in `~/.codex/config.toml`.

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
export CGH_ROOT=/path/to/project
```
