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

---

---

[Back to the README](../README.md)
