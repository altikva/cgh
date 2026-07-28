# Proposal 003: guard, enforcing confidentiality on the agent side

Status: draft, for discussion. Depends on 001 (plugins, findings,
integrations surface) and 002 (classification as egress policy). Based
on the maintainer's survey of agent hook surfaces (2026-07).

## Why

PII findings and confidentiality labels are only worth collecting if
they are enforced by default. The enforcement point is not cgh's own MCP
responses (cgh controls those already, proposal 002); it is the agent's
NATIVE tools: Read, Grep, Glob, `cat` through a shell tool. An agent
that bypasses cgh and reads `payroll.xlsx` directly defeats the whole
chain. Every serious agent CLI now exposes a hook surface that can veto
or rewrite tool calls; cgh should install guard hooks there by default,
per agent, through the AgentIntegration surface added to proposal 001.

## What the guard does

One fast command, `cgh _hook_guard`, designed to sit in an agent's
pre-tool-use hook:

1. Reads the hook payload (tool name + arguments) on stdin.
2. Extracts the file path(s) targeted by the call.
3. Looks them up in the Finding store.
4. Answers with the agent's decision protocol: deny when the file
   carries a `confidential` finding or any block-severity finding,
   otherwise allow. On agents that support output rewriting, a
   post-tool-use variant can redact `pii.*` spans instead of blocking.

The deny message names the reason ("blocked by cgh guard: file labeled
confidential") so the agent can relay something actionable instead of a
silent failure.

Latency budget: the hook fires on every native read of the agent, so
the guard must answer in single-digit milliseconds. Direct SQLite read,
no owner round-trip, no Python import of the heavy modules on the hot
path.

## Required change to proposal 001: findings live in SQLite

Proposal 001 sketched `Finding` as a graph node table. That does not
survive contact with enforcement: DuckDB holds an exclusive write lock
while an owner is alive, so a guard hook could not read findings exactly
when they matter most (a live session). Findings therefore live in
SQLite (`.codegraph/findings.db`, WAL mode), which serves concurrent
readers while the owner writes, works when the owner is down, and is
what the guard, the CLI, and the MCP tools all read. The graph keeps
only what benefits from joins (nothing, initially). FTS feeding
(001 decision 2) is unaffected.

## Per-agent adapters, honest about capability

Enforcement quality differs per agent. The maintainer's survey
(2026-07, to re-verify at implementation time since these surfaces move
fast):

| Agent | Events | Veto | Rewrite | Guard level |
|---|---|---|---|---|
| Claude Code | ~30 | ~13 blocking, exit 2 or `permissionDecision: deny` | `updatedInput`, `updatedToolOutput` | enforce + redact |
| Copilot | 8 | `preToolUse` only | no | enforce (read block only) |
| Kiro | pre/post tool use | exit code 2 | no | enforce (read block only) |
| Gemini CLI | pre/post tool use | none verified | no | advisory |
| Codex | pre/post tool use | none verified | no | advisory |

The `AgentIntegration` protocol (001 amendment) gains one member:

```python
def guard(self) -> GuardSpec | None:
    # hook events to write, handler command, decision protocol,
    # capability: "enforce+redact" | "enforce" | "advisory" | None
```

`cgh guard status` reports the truth per detected agent: enforced,
advisory (the hook can only warn), or unprotected (no hook surface, the
only barrier is cgh's own MCP gate). No false comfort: an agent listed
as advisory or unprotected is stated as such.

### Claude Code adapter, two layers

Claude Code's own docs recommend the permissions system over hooks for
strict allow/deny (the `if` matcher fails open on unparseable Bash
commands). So the adapter uses both mechanisms:

1. **Dynamic layer**: `PreToolUse` hook on `Read|Grep|Glob|Bash`,
   command handler `cgh _hook_guard`, deny via exit 2 / stderr. Plus an
   optional `PostToolUse` handler using `updatedToolOutput` to redact
   `pii.*` spans from tool results in redact mode.
2. **Static layer** (strict posture): the owner syncs explicit
   `Read(<path>)` deny rules into `.claude/settings.local.json` for
   currently-confidential paths whenever findings change. Static rules
   hold even where hook matching fails open; the price is a visible
   deny list, which is itself metadata (the file names of confidential
   files appear in the settings file). `guard_static = true` opts in,
   documented tradeoff.

cgh already installs Claude Code hooks (`_hook_precheck_read`,
`_hook_precheck_grep`) and git hooks, so the plumbing and the
idempotent-write discipline exist.

## Posture and defaults

- Guard hooks are installed **by default** during `cgh init` / `cgh
  setup` for every detected agent whose integration declares a
  GuardSpec, listed in the init summary, opt-out per agent
  (`cgh guard disable <agent>`). Enforced by default is the point of
  the whole chain; consent is the init step itself.
- **Fail posture**: a guard that crashes must not silently fail open in
  strict mode. `egress = "open"` (002 knob): internal errors allow with
  a logged warning. `egress = "strict"`: internal errors deny (exit 2),
  a broken guard reads as "blocked" rather than "leaked".
- Every deny and every redaction is logged to `activity.log`, same
  audit stream as 002's egress log: one place answers "what was
  blocked, what left".

## Limits, stated plainly

- This is policy enforcement inside cooperating agent frameworks, not a
  sandbox. An agent free to run arbitrary shell can read anything the
  OS allows; hook matchers on `Bash` narrow, not close, that path.
  OS-level controls are out of scope for cgh.
- Coverage is per-agent and degrades to advisory or nothing on agents
  without a veto surface; `cgh guard status` is the honest map.
- Findings lag reality: a file becomes confidential when scanned or
  labeled, not when created. The watcher plus deferred queue keep the
  window small; strict mode plus `require_label` (002) close it for
  organizations that need allowlist semantics.

## Open questions

1. Should redact mode (PostToolUse `updatedToolOutput`) ship in v1 of
   the guard, or after block mode has soaked? Redaction rewrites agent
   context and is harder to verify.
2. Static deny-rule sync for Claude Code: opt-in (proposed) or default
   in strict mode?
3. `Bash` matching: block-list of read-ish commands (`cat`, `head`,
   `sed`, `rg` on flagged paths) or deny any Bash whose arguments
   contain a flagged path (simpler, more false positives)?
