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
   - If `fresh=false`, proceed.

2. **Preferred**: call `mcp__codegraph__incremental_reindex`. It compares
   stored per-file git blob SHAs to the current HEAD and re-indexes only
   the files whose content changed. Handles deletions too. Fast and
   correct for branch switches, pulls, rebases.

3. If `incremental_reindex` returns `mode=fallback_full` (old index
   without blob tracking), it already ran a full scan — you're done.

4. Only call `mcp__codegraph__scan_repo` if the user explicitly asks
   for a full rebuild.

5. Report the result to the user briefly (e.g. "Refreshed codegraph —
   12 files re-indexed, 2 deleted.").

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
