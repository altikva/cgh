# Proposal 004: shared memory, deepening the third pillar

Status: accepted 2026-07-29. Builds on 001 (agent integrations
surface), 002 (summaries as substrate), 003 (hook adapters, mode).
Mostly core work, not a plugin: memory is one of cgh's three founding
pillars, not an extension.

## Why

cgh's founding goals: cut token usage, make files understandable, and
serve as shared memory for LLMs, so that clearing an agent's context
costs nothing: instructions and learnings survive outside the context
window. The first two pillars got the recent investment (002). This
proposal pays down the third, which is also the one where cgh has the
fewest competitors: agent-native memory is siloed per tool, per
project, per session; cgh sits below all of them with an MCP surface
every agent can reach.

## What exists today

- The **knowledge store**: `knowledge_record / search / list / terms /
  forget`, persisted per repo, already survives compaction and clears.
- **compact_session**: a manual digest tool the agent is instructed to
  call when context runs low.
- **Memory and plan indexing**: cgh indexes Claude Code's per-project
  memory directory and plans, searchable via `memory_search` /
  `plan_search`.
- **context_for_task**: merges code, memory, and plans for a task
  kickoff.
- The MCP instructions tell agents to reload knowledge after a clear.

## The gap

Three weaknesses, all visible in daily use:

1. **The ritual is manual and trust-based.** Saving before a clear and
   reloading after depend on the model following instructions under the
   worst conditions (context nearly full, or freshly emptied). What the
   model forgets to save is lost; what it forgets to reload is
   re-derived at full token price.
2. **Rehydration is a multi-call scavenger hunt**: `knowledge_list`,
   then `knowledge_search`, then `memory_search`, then `plan_search`.
   Four calls, unranked results, no budget control.
3. **Memory is single-agent in practice.** cgh reads Claude Code's
   memory files; Gemini, Codex, and Cursor each keep their own silo.
   What Claude learned yesterday, Gemini re-derives today.

## Design

### 1. Checkpoint and resume, one call each

Two MCP tools become the whole session-continuity protocol:

- `checkpoint(session_id, digest?, learnings?)`: persists a session
  snapshot: the digest (agent-written or omitted), any pending
  learnings as knowledge entries, open threads. Cheap enough to call
  often; idempotent per session_id.
- `resume(session_id?, task?, budget_kb?)`: returns ONE composed,
  ranked, budgeted bundle: standing instructions, the latest relevant
  digests, knowledge entries matching the task, open plans, and the
  summaries (002) of recently touched files. Default `budget_kb` keeps
  the bundle a small fraction of a context window; ranking is
  recency + FTS relevance, upgraded to embeddings when cgh-embed is
  installed.

`context_for_task` remains the task-scoped view; `resume` is the
session-scoped one. Both share the ranking and budget machinery.

### 2. Automatic lifecycle through agent hooks

The manual ritual disappears where the agent's hook surface allows it
(maintainer's survey, proposal 003):

- **Claude Code**: a `PreCompact` hook with an `mcp_tool` handler calls
  `checkpoint` automatically before compaction; a `SessionStart` hook
  injects the `resume` bundle at session open. No model cooperation
  needed: the harness does it.
- **Agents with weaker surfaces**: degrade to instructions (what we
  have today), stated honestly in `cgh guard status`-style reporting.

These hooks ship through the same AgentIntegration surface as 003's
guard hooks, installed at `cgh init`, opt-out per agent.

### 3. One brain, many agents

The knowledge store becomes the canonical cross-agent memory; agent
silos become read-only tributaries:

- Each AgentIntegration (001) can declare where its tool keeps native
  memory (Claude's auto-memory directory today; others as they appear).
  cgh indexes them read-only, tagged by origin.
- Writes go through cgh (`knowledge_record`, `checkpoint`), so a
  learning recorded during a Claude session is served to a Gemini
  session an hour later through the same MCP tools.
- cgh never writes into an agent's native memory files: one canonical
  store, no sync conflicts, origin always visible.

### 4. Standing instructions

"Consignes" that emerge mid-conversation (corrections, preferences,
project rules) die with the context today unless the agent thinks to
save them. A dedicated knowledge kind, `standing_instruction`, gets
first place in every `resume` bundle, and the MCP instructions tell
agents to record corrections there the moment they happen. Hygiene
applies (see 5): superseded instructions link to their replacement
instead of accumulating contradictions.

### 5. Memory hygiene

Memory that only grows becomes noise that costs tokens, the exact
opposite of pillar 1:

- **Supersede links**: a knowledge entry can replace an older one; the
  old entry stops appearing in bundles but stays queryable.
- **Decay**: entries unread for a configurable window drop in rank,
  never silently deleted.
- **`cgh memory review`**: a CLI pass listing stale, contradictory, or
  never-recalled entries for human pruning; also exposed as an MCP
  tool so an agent can be asked to tidy its own memory.

### 6. Memory is subject to the same protections

Memory content can be as sensitive as file content (a digest can quote
a confidential file). Knowledge entries and digests pass through the
same scanners as files (002), carry findings, and the guard (003) and
egress gate apply to them identically: in secure mode, a digest quoting
a confidential file inherits its flag and never reaches a cloud
backend, and `resume` withholds flagged entries from agents whose
transport would leak them. One rule everywhere: findings gate what
leaves, whatever shape it left in.

## What this is NOT

- Not a vector database product: embeddings only rank, they are
  optional (cgh-embed), and the store stays plain SQLite + files.
- Not agent-memory sync: cgh never writes into agents' native stores.
- Not infinite context: `resume` is budgeted by design; the point is a
  small, dense bundle, not a replay of everything.

## Decisions (2026-07-29)

1. **Knowledge federates read-only, opt-in per query.** Default stays
   strictly per repo (001 unchanged as the default behavior); passing
   `scope="all"` to `knowledge_search` or `resume` fans out read-only
   to the federation's children, scope-tagged like everything else.
   Never on by default: cross-repo learnings are reachable when the
   agent asks, noise never leaks into bundles unrequested.
2. **Auto-checkpoint triggers, v1**: `PreCompact` and `SessionEnd`,
   the two reliable events. A token-threshold trigger (context passing
   ~80%) is supported where the agent surface exposes the signal, off
   by default: too-frequent checkpoints dilute digests.
3. **Resume is hybrid.** `SessionStart` injects only a two-to-three
   line header ("cgh holds a resume bundle for this session: N
   standing instructions, N digests, N open plans; call `resume` to
   load it"); the full budgeted bundle loads on demand. The model
   cannot forget what it was just shown, and the bundle is only paid
   for when used. Pillar 1 stays honored.
