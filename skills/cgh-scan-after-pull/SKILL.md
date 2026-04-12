---
name: cgh-scan-after-pull
description: Refresh the codegraph index after git operations that change many files. Trigger when the user mentions `git pull`, `git rebase`, `git merge`, `git checkout <branch>`, or "I just pulled/switched branches". Ensures symbol_lookup returns accurate results.
---

# Refresh codegraph after git operations

Codegraph's file watcher keeps the graph fresh for individual saves, but it
does NOT observe the many file changes that happen during a `git pull`,
`rebase`, `merge`, or branch `checkout`. After those, the graph may point at
line numbers that no longer exist, or miss symbols introduced by the change.

## When to call

Trigger immediately after the user mentions (or runs via Bash) any of:

- `git pull`
- `git rebase`
- `git merge`
- `git checkout <branch>` or `git switch <branch>`
- "I just pulled"
- "Switched to [branch]"
- "Rebased onto main"

## How to refresh

1. Call `mcp__codegraph__scan_status` to see how stale the graph is.
   - If `fresh=true`, do nothing.
   - If `fresh=false`, note `behind_by` and `changed_files`.

2. If only a handful of files changed (`< 50`), prefer
   `mcp__codegraph__index_changed_files(since="<indexed_sha>")` — it's fast.

3. If many files changed or you're unsure, call `mcp__codegraph__scan_repo()`.
   A full scan on a typical repo is under 2 seconds.

4. Report the result to the user briefly (e.g. "Refreshed codegraph — 12
   files re-indexed.").

## Don't

- Don't reindex on every unrelated message; only when a git operation that
  changes many files has just occurred.
- Don't ask the user to restart the MCP server — `scan_repo` handles it
  live through the existing connection.
- Don't re-scan if `fresh=true`; it wastes time and load.

## Token-saving rule of thumb

Calling `scan_status` costs ~1 JSON blob. Running `scan_repo` only when
needed saves you from relying on stale `symbol_lookup` results and then
having to manually verify with file reads.
