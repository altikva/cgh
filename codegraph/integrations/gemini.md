# codegraph for Google Gemini CLI

## Setup

Gemini CLI supports MCP servers via configuration.

```bash
# Initialize codegraph
cgh init
cgh index
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

Prefer codegraph tools over reading entire files: they return exact file:line references.
```

## Environment variable

```bash
export CODEGRAPH_ROOT=/path/to/project
```
