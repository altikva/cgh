---
name: cgh-add-dir
description: Add an external directory (sibling repo, related project) to the codegraph index so cross-repo symbol lookups work. Use when the user asks to include another repo/folder in the graph ("add ../frontend to codegraph", "include the infra repo in the graph", etc.).
---

# cgh add-dir

Add an external directory to the codegraph index with hot reload — no MCP restart needed.

## When to use

The user wants to extend the current codegraph with another directory:
- "Add ../ondonne-frontend to codegraph"
- "Include the infra repo in the graph"
- "Can you index the shared-lib folder too?"

## How to add a directory

**Prefer the MCP tool** — it handles everything in one call:

1. Call `mcp__codegraph__add_directory` with the path (absolute or relative to the repo root).
2. Report the result to the user (indexed count, whether the watcher was extended).

The tool:
- Persists the path to `.codegraph/config.toml` (`extra_dirs`)
- Scans all parseable files immediately
- Extends the running file watcher if possible (hot reload — no restart)
- Returns JSON with status + counts

## Fallback: CLI

If the MCP tool isn't available or fails, fall back to the CLI:

```bash
cgh add-dir add <path>     # add + persist
cgh add-dir list           # show configured extras
cgh add-dir remove <path>  # remove
```

After a CLI `add`, you must tell the user to either:
- Ask you to re-run a scan (`mcp__codegraph__scan_repo`), OR
- Restart the MCP server (`/mcp` in Claude Code → reconnect codegraph)

The MCP-tool path is preferred because it avoids the restart step.

## Verifying it worked

After adding, you can confirm:
- `mcp__codegraph__graph_stats` — node counts should have increased
- `mcp__codegraph__search_symbols` with a symbol from the new dir — should return a hit

## Don't

- Don't ask the user to run `cgh index` manually — the MCP tool handles indexing.
- Don't restart the MCP server yourself; only suggest the user do it if the hot-extend fails.
- Don't add directories outside the user's intent (e.g., don't automatically pull in `node_modules` or build outputs).
