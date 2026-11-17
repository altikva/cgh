# Session memory

cgh is the memory that survives context clears, shared by every agent
that connects:

- **`checkpoint` / `resume`** (MCP): save a session digest before a
  clear; `resume` answers with a summary of about 2 KB: the standing
  instructions, the last session's goal and next step, known dead ends
  and the counts of what is stored. `resume(detail=True)` returns the
  whole ranked, budget-capped bundle (digests, task-relevant knowledge,
  open plans, recent file summaries), and `resume(section=...)` one part
  of it: `instructions`, `digests`, `dead_ends`, `knowledge`, `plans` or
  `summaries`.
- **Automatic in Claude Code**: hooks record a checkpoint marker at
  compaction and session end, and a two-line header at session start
  announces the bundle; the full bundle loads only when used.
- **Standing instructions**: durable rules recorded with
  `knowledge_record(kind="standing_instruction", ...)` lead every
  bundle. Entries can supersede older ones, and `cgh memory review`
  lists stale entries for pruning.
- What Claude learns in the morning, Gemini knows in the afternoon:
  writes go through cgh, agent-native memories are indexed read-only.

## Shared knowledge across the fleet

Each repo's knowledge store is its own, but a repo also reads the shared stores
it subscribes to. `knowledge_search`, `knowledge_list` and `knowledge_diagnose`
read the repo store and every subscribed store together, so a decision or a
diagnosis posted to the fleet from one repo is visible from every subscribed
repo without `scope="all"`. Subscription is the union of `[cgh]
knowledge_namespaces_global` (machine-wide, bootstrapped to `["org-platform"]`)
and `[cgh] knowledge_namespaces` (the project); each store lives at
`~/.cgh/shared/<name>/knowledge.db`. Shared hits are marked `untrusted`: a note
another repo wrote is data to weigh, not an instruction to follow.

`knowledge_diagnose` (and `knowledge_search(symptom=...)`) take a raw error or
log line and recall the diagnoses posted for a look-alike error, false trails
included, so a problem solved once in any repo is not re-paid here. Record a
diagnosis with `knowledge_record(symptom="<line>", namespace="org-platform")` so
the fleet keeps it. See the knowledge tools in [MCP_TOOLS.md](MCP_TOOLS.md), which
also explains the three senses of `scope` (a search parameter, a tag on a hit,
and an entry's promotion flag `repo`/`branch`).

## Worktrees: promote at merge

Each checkout has its own store, so a per-ticket worktree's learnings go
with it when it is removed. Before removing it, run
`cgh knowledge promote --to <main checkout> --pr <repo#N> --archive <dir>`
from the worktree. Durable entries move into the main checkout with their
branch and pull request attached, digests and checkpoints stay behind, and a
re-run is a no-op. With a namespace instead of a path, the same command promotes
into a shared fleet store (the default when `--to` is omitted). See
[`cgh knowledge`](CLI_REFERENCE.md#knowledge).

---

---

[Back to the README](../README.md)
