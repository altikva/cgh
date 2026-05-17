# codegraph for Google Gemini CLI

## Setup

Gemini CLI supports MCP servers via configuration.

```bash
# Initialize codegraph
codegraph init
codegraph index
```

Add to `.gemini/settings.json` or project MCP config:

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

## GEMINI.md instructions

Add to `GEMINI.md` to teach Gemini to use codegraph:

```markdown
## Code Navigation with codegraph

This project is indexed by codegraph. Use its MCP tools for efficient navigation:

- symbol_lookup(name) — find where any function/class/section is defined
- context_for_task(task) — build ranked context from the code graph + docs
- search_symbols(query) — fuzzy search across all symbols
- search_docs(query) — search markdown documentation
- find_callers(fn) — who calls this function?
- find_callees(fn) — what does this function call?
- doc_outline(file) — table of contents for markdown files
- visualize_graph(scope) — generate Mermaid diagrams of relationships

Prefer codegraph tools over reading entire files — they return exact file:line references.
```

## Environment variable

```bash
export CODEGRAPH_ROOT=/path/to/project
```
