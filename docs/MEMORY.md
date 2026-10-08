# Session memory

cgh is the memory that survives context clears, shared by every agent
that connects:

- **`checkpoint` / `resume`** (MCP): save a session digest before a
  clear; get back ONE ranked, budget-capped bundle: standing
  instructions first, then digests, task-relevant knowledge, open
  plans, and recent file summaries.
- **Automatic in Claude Code**: hooks record a checkpoint marker at
  compaction and session end, and a two-line header at session start
  announces the bundle; the full bundle loads only when used.
- **Standing instructions**: durable rules recorded with
  `knowledge_record(kind="standing_instruction", ...)` lead every
  bundle. Entries can supersede older ones, and `cgh memory review`
  lists stale entries for pruning.
- What Claude learns in the morning, Gemini knows in the afternoon:
  writes go through cgh, agent-native memories are indexed read-only.

## Worktrees: promote at merge

Each checkout has its own store, so a per-ticket worktree's learnings go
with it when it is removed. Before removing it, run
`cgh knowledge promote --to <main checkout> --pr <repo#N> --archive <dir>`
from the worktree. Durable entries move into the main checkout with their
branch and pull request attached, digests and checkpoints stay behind, and a
re-run is a no-op. See [`cgh knowledge`](CLI_REFERENCE.md#knowledge).

---

---

[Back to the README](../README.md)
