# Design: the scope of a cgh server is decided per caller

Status: proposed (2026-11-28), not reviewed yet. Four choices are the
maintainer's to make. The body follows the recommended option for each and
marks the place; the last section, "Open questions for the review", gives the
other option and what each one costs.

## Context

One owner process serves a checkout. Every `cgh serve` is a stateless
stdio to HTTP bridge to it, and every `cgh` command that needs the graph asks
it over the same loopback port. The owner answers each of them with the same
reach, and that reach goes beyond the checkout:

| What is read outside the repo | Where it is decided today |
|---|---|
| Sibling repos, by `file_at_ref(repo=)` and `pattern_search(repo=)` | `sibling_repos` in `cgh/analysis/refs.py`: the user's global `[cgh] siblings`, the project's own, and the federated subrepos |
| Shared knowledge stores under `~/.cgh/shared/` | `resolved_knowledge_namespaces` in `cgh/core/config.py`, read by `knowledge_search`, `knowledge_list`, `knowledge_diagnose` and `decide` |
| Claude Code memory of the project | `memory_dir` in `cgh/core/config.py`: `CGH_MEMORY_DIR`, then `[paths] memory_dir`, then `~/.claude/projects/<slug>/memory` |
| Claude Code plans (one directory for every project of the profile) | `plans_dir`, same order, ending at `~/.claude/plans` |

A session that should see one repository therefore sees the others: the
siblings declared in the global config stay readable, and the shared stores
come back in every knowledge search. Three facts make the existing knobs
useless for narrowing one session:

1. **`CLAUDE_HOME` is the constant `~/.claude`.** `CLAUDE_CONFIG_DIR` is
   ignored. A session running under another Claude profile is served the
   memory and the plans of the default profile by `memory_search`,
   `plan_search` and `resume`, and its own memory is never indexed.
2. **`CGH_MEMORY_DIR` and `CGH_PLANS_DIR` are read by the owner.** The owner
   is unique per checkout, shared by every session, and carries the
   environment of whichever `cgh serve` spawned it first. A second session
   that sets the variables changes nothing.
3. **The memory index is one table in the shared `.cgh/`.** `scan_memory_dir`
   and `scan_plan_dir` delete every row whose file is not in the directory
   they scan. Two owners over time, or two scans with different settings,
   undo each other.

The fix is not another owner-side setting: the owner is shared, so anything it
reads from its own environment or config applies to every session alike. The
scope has to arrive with the call.

What already exists and is reused:

| Piece | Where |
|---|---|
| A per-request header from every caller, read inside a tool call | `X-Cgh-Origin`, sent by `proxy_stdio_to_http` (`cgh/state/ipc.py`) and `cgh/cli/owner_client.py`, read by `_call_origin` in `cgh/server/__init__.py` |
| The proxy restarts an owner that runs another cgh version | `owner_version_current` in `cgh/state/serve.py` |
| The only authentication: a bearer key in `.cgh/auth.key`, mode 600 | `_BearerAuth` in `cgh/server/__init__.py` |
| Shared store names checked against an allowlist | `valid_shared_namespace` in `cgh/state/call_log.py` |
| One function resolves every `repo=` argument | `resolve_repo` in `cgh/analysis/refs.py` |

## Scope of this document

In scope: the form of the scope a caller declares and who sets it, what the
owner does with it in each tool that reads outside the repository, the default
when it is missing, the `cgh serve` options, the memory and plan index, what a
lying caller obtains, the delivery steps and the refusal tests.

Out of scope:

- **Confining the agent.** The scope narrows what the cgh tools serve. An
  agent with a shell reads a sibling repo with `cat`. Keeping files out of an
  agent's reach stays the job of that agent's permission rules or sandbox, as
  `docs/SECURITY.md` already says.
- **Federated subrepos and `extra_dirs`.** They are declared by the checkout's
  own config and indexed as part of the workspace; they are what the session
  was given. This is the recommended option of review question 1.
- **Authenticating callers.** No per-caller key, no signature. See "What a
  lying caller obtains".
- **The repo's own knowledge store**, its digests and its standing
  instructions: they live in `.cgh/` and belong to the checkout.

## The scope a caller declares

The owner does not need to know who is calling. It needs to know what this
caller was given. What travels is a declaration of scope, not an identity of a
person or a session.

### Form

One HTTP header on every request to the owner:

```
X-Cgh-Scope: {"v":1,"siblings":false,"shared":["org-platform"],"claude_state":true,"claude_home":"/Users/x/.claude-work"}
```

Compact JSON, ASCII only (`ensure_ascii`), at most 4 KB. Every key but `v` is
optional, and a missing key means "the default for this key".

| Key | Type | Missing means | Meaning when present |
|---|---|---|---|
| `v` | int | (required) | Format version, `1` |
| `siblings` | bool | `true` | `false`: no sibling repo is readable |
| `shared` | list of names | every store the repo subscribes to | The only shared stores this caller reads or writes. `[]`: none |
| `claude_state` | bool | `true` | `false`: no Claude Code memory, no plans |
| `claude_home` | absolute path | the owner's default | The Claude Code config directory of the caller |
| `memory_dir` | absolute path | resolved from config, then `claude_home` | The caller's `CGH_MEMORY_DIR` |
| `plans_dir` | absolute path | resolved from config, then `claude_home` | The caller's `CGH_PLANS_DIR` |

The header is sent with each request and evaluated on each request. It is not
tied to the MCP session id: a proxy that respawns its owner mid-session keeps
its scope without a new handshake, and the owner keeps no table of callers.

### Who sets it

The process that bridges to the owner, from what it alone knows:

- **`cgh serve`**, from its command line (the narrowing options below) and
  its own environment (`CLAUDE_CONFIG_DIR`, `CGH_MEMORY_DIR`,
  `CGH_PLANS_DIR`, and the legacy `CG_` spellings). The command line is
  written in the MCP config of the session by whoever launches it; the
  environment is the one the agent's client passes to its MCP servers.
- **The CLI client** (`cgh/cli/owner_client.py`), from its own environment.
  A `cgh` command typed in a shell under `CLAUDE_CONFIG_DIR` then asks for
  the right profile.

The agent cannot set it: it talks MCP over stdio to a proxy whose options were
fixed at launch, and no tool takes a scope argument.

A proxy with nothing to say (no option, none of the variables set) sends no
header at all, so its requests stay byte for byte what they are today.

### A scope narrows or redirects, it never grants

Two kinds of keys, and no third:

- `siblings`, `shared` and `claude_state` only remove. `shared` is
  intersected with what the owner would have read anyway for a default
  search; it cannot subscribe the repo to a store.
- `claude_home`, `memory_dir` and `plans_dir` replace a directory by another.
  They do not add a second one: a caller reads one memory directory and one
  plans directory.

Nothing in a scope adds a sibling, a subrepo, or a path the graph tools read.
The trust guards on a project config (`_project_config_trusted`, the local
index and workspace boundary checks) are unchanged and run before the scope is
applied.

## What the owner does with it

### Reading the scope

A new module `cgh/core/scope.py` holds a frozen dataclass `CallerScope`, the
parser of the header and the function `current_scope()`. The parser runs in an
HTTP middleware placed after `_BearerAuth`; it stores the result in a
context variable that `current_scope()` reads, so a tool never parses a header
itself. Outside an HTTP request (an in-process call, the watcher, the startup
scan) `current_scope()` returns the default scope.

The middleware answers every request that carried a scope header with
`X-Cgh-Scope-Applied: 1`. The proxy uses it, see "Version skew".

### Missing, malformed, unknown

| Request | Owner |
|---|---|
| No `X-Cgh-Scope` | Default scope: today's behaviour, owner environment included |
| Valid header | That scope |
| Not JSON, not an object, over 4 KB, a wrong type, a relative path, an invalid store name | HTTP 400, `{"error": "invalid scope: <reason>"}`. The tool does not run |
| `v` other than `1` | HTTP 400, `{"error": "unsupported scope version"}` |
| An unknown key | HTTP 400. A caller that asked for a restriction this owner does not know must not get an unrestricted answer |

A broken scope never degrades to the default: the default is the widest
scope, and falling back to it would turn a typo into full access.

### Per tool

| Tool | Reads outside the repo | With the scope |
|---|---|---|
| `file_at_ref(repo=)`, `pattern_search(repo=)` | a sibling work tree | `siblings: false`: `resolve_repo` refuses any non-empty `repo` that is not a federated subrepo. `repo=""` and `ref=` on this repo are untouched |
| `knowledge_search`, `knowledge_list`, `knowledge_diagnose`, `decide` (default read) | the subscribed shared stores | the subscribed stores intersected with `shared` |
| the same, with `namespace="<shared name>"` | that store | refused when the name is not in `shared` |
| `knowledge_record(namespace="<shared name>")`, shared promotion | writes that store | refused when the name is not in `shared` |
| `memory_search`, `memory_list`, `memory_rescan` | the memory directory | `claude_state: false`: refused. Otherwise the caller's memory source (see the index section) |
| `plan_search`, `plan_list`, the plan rescan | the plans directory | same |
| `resume` | open plans | `claude_state: false`: the plans section is left out and `counts.plans` is absent, not `0` |
| `context_for_task` | memory and plan hits under "See also" | `claude_state: false`: those hits are left out; `shared` filters the knowledge hits |
| `check_action` | nothing (repo store only) | unchanged |
| every graph tool, federation fan-out included | nothing outside the workspace | unchanged |

All four default readers of shared stores go through `_resolved_namespaces`
in `cgh/server/tools_knowledge.py`; the intersection is made there, once.
`resolve_repo` is the single door for `repo=`. The memory and plan tools reach
the index through `get_fts_conn` plus a source filter (below). No tool gets a
private rule.

### Refused or empty

This section follows the recommended option of review question 2.

A tool the caller asked for by name answers a refusal, not an empty result:

```json
{"error": "out_of_scope",
 "scope": "claude_state",
 "message": "this session was started without Claude Code memory and plans (--no-claude-state)"}
```

`scope` names the key that refused (`siblings`, `shared`, `claude_state`).
An empty list would read as "nothing is known", and an agent acts on that: it
re-derives a decision that exists, or concludes a symbol is absent from a
sibling.

A tool that only folds outside data into a wider answer (`knowledge_search`
default read, `resume`, `context_for_task`) leaves the part out and says so
with a top-level `"scope_applied": ["shared"]`, listing the keys that removed
something from this answer. The key is absent when the scope removed nothing,
so answers under the default scope do not change.

The call log records refusals with the tool, the origin and the refusing key,
so a session that keeps hitting its fence shows up in `cgh stats`.

## The options of `cgh serve`

| Option or variable | Header key | Effect |
|---|---|---|
| `--no-siblings` | `siblings: false` | No `repo=` read of a declared sibling |
| `--no-claude-state` | `claude_state: false` | No memory, no plans, in any tool |
| `--shared-stores a,b` | `shared: ["a","b"]` | Only these shared stores |
| `--no-shared-stores` | `shared: []` | No shared store. Exclusive with `--shared-stores` |
| `CLAUDE_CONFIG_DIR` | `claude_home` | The profile whose memory and plans are served |
| `CGH_MEMORY_DIR`, `CGH_PLANS_DIR` | `memory_dir`, `plans_dir` | As today, but for this caller only |

A name passed to `--shared-stores` that fails `valid_shared_namespace` makes
`cgh serve` exit with an error before it bridges anything. A valid name the
repo does not subscribe to is kept: it allows an explicit
`namespace="<name>"` read, as today, and adds nothing to default searches.

`--no-claude-state` wins over the three directory variables: the proxy then
sends none of them.

### Honouring `CLAUDE_CONFIG_DIR`

The constant `CLAUDE_HOME` becomes a function, `claude_home(env)`, that
returns `CLAUDE_CONFIG_DIR` when set and `~/.claude` otherwise. Who calls it
decides whose profile is read:

- a caller (the proxy, the CLI client, a `cgh` command that runs in process
  without an owner) reads its own environment;
- **the owner never reads `CLAUDE_CONFIG_DIR` from its own environment.** It
  takes `claude_home` from the scope, and `~/.claude` when the scope has
  none. Its environment is the first caller's, by accident of spawn order.

For the same reason, once a request carries a scope header the owner ignores
its own `CGH_MEMORY_DIR` and `CGH_PLANS_DIR` for that request. Resolution per
call, first match wins:

1. `memory_dir` of the scope;
2. `[paths] memory_dir` of the project config;
3. `<claude_home of the scope>/projects/<slug>/memory`;
4. only when the request has no scope header: the owner's `CGH_MEMORY_DIR`,
   then `~/.claude/projects/<slug>/memory`, as today.

Plans follow the same order with `plans_dir` and `<claude_home>/plans`.

Before using a directory named by a scope, the owner checks that it is
absolute, exists, is a directory and, on POSIX, belongs to the owner's own
user. Anything else is refused as an invalid scope (HTTP 400).

### Version skew

The header only works when both ends understand it. This section follows the
recommended option of review question 3: a narrowed session fails closed.

**Who can be affected.** Only a proxy started with a narrowing option or one
of the three variables sends a header, and only such a proxy expects the
acknowledgement. A session launched as today sends nothing and expects
nothing: whatever the state of the owner, an upgrade cannot break it through
this mechanism. That is what keeps a partial upgrade from blocking every
session of the machine.

**Old proxy, new owner.** No header, default scope: nothing changes.

**New proxy, owner that does not know the header.** The header would be
ignored and the restriction silently dropped. Two layers prevent it:

1. The existing version check. A proxy that finds an owner running another
   cgh version than the installed one stops it and spawns a new one, from the
   same install as the proxy itself. The other sessions' proxies meet a
   refused connection and re-attach, as they do today.
2. The acknowledgement, for what the version check misses (a development
   checkout whose version string did not move, an owner that could not be
   stopped). The first request of a session is `initialize`, which is safe to
   send twice. If its response lacks `X-Cgh-Scope-Applied`, the proxy
   restarts the owner once and sends `initialize` again.

If the acknowledgement is still missing, the proxy keeps the session alive:
`initialize`, `tools/list` and `ping` are forwarded, so the agent's client
does not drop the server. Each `tools/call` is answered by the proxy with a
JSON-RPC error, and the owner's answer is discarded:

```
cgh: this session is limited (--no-siblings) but the owner serving
<root> did not apply the limit (owner version <x>, installed <y>).
Nothing was returned. Run `cgh stop --root <root>` and retry.
```

The user sees every cgh tool of that one session fail with that sentence, and
`cgh status` shows the mismatch. The other sessions of the checkout keep
working.

**Owner down.** Unchanged. A refused connection makes the proxy respawn or
re-attach and retry, and the new owner acknowledges. A request the owner
dropped after receiving it is reported as today and not resent.

**Owner slow or hung.** Unchanged. The proxy sets no read timeout and the MCP
client bounds the wait. A missing acknowledgement is only ever concluded from
a response that arrived: a timeout is never read as "scope not applied", so a
long reindex or codegen call cannot trip the check.

**Owner answers an error.** The acknowledgement is required to forward a
successful body. A 400 for an invalid scope and a 401 for a bad key are
forwarded as errors: they carry nothing from outside the scope.

## The memory and plan index

Today `memory_entries` and `plan_entries` are keyed by file path and assumed
to mirror one directory. With a per-caller directory, one table has to hold
several directories without one scan erasing another.

- Both tables gain a column `source`: the resolved absolute directory the row
  was indexed from.
- `scan_memory_dir` and `scan_plan_dir` take the directory as an argument
  instead of resolving it themselves. They compare, upsert and purge only
  among the rows of that `source`.
- Every read (`memory_search`, `list_memory_entries`, `plan_search`,
  `list_plan_entries`, the plan section of `resume`, the hits of
  `context_for_task`) filters on the `source` resolved from the scope of the
  call.
- Migration, in the existing schema upgrade path of `cgh/core/fts.py`: the
  column is added and backfilled with the parent directory of `path`. Both
  scans glob `*.md` without recursion, so the parent is exact.

Freshness:

- The owner scans a source the first time a call names it. The scan is the
  existing mtime comparison, so a source already indexed costs one directory
  listing.
- The watcher watches the default source from start, as today, and adds a
  source when it is first seen, up to 8 sources. Past that, a source is
  rescanned at call time when its directory mtime moved.
- At owner start, rows whose `source` no longer exists on disk are removed.

The index stays in the checkout's `.cgh/fts.db`. Rows from two profiles then
sit side by side in a file both sessions' owner can read, which is acceptable:
the filter is what keeps them apart for the tools, and the file itself was
never a boundary (see the next section).

`claude_state: false` does not empty the index: another session of the same
checkout may be entitled to it. It only refuses the reads of this caller.

Plans are not partitioned by project, because Claude Code keeps one plans
directory per profile. A caller with `claude_state` on sees the plans of every
project of its profile, as today.

## What a lying caller obtains

A caller that reaches the owner holds `.cgh/auth.key`. That file is mode 600
in a mode 700 directory, so the caller runs as the user who owns the checkout.
It can already read every sibling repo, every shared store and every Claude
profile of that user straight from disk, without cgh.

The scope is therefore not a security boundary, and this design does not try
to make it one. This is the accepted limit, and the recommended option of
review question 4. A lying caller is any process that reaches the owner
without the proxy its session was launched with: an agent that reads
`auth.key` from its checkout and calls the loopback port with `curl`, or that
runs a `cgh` command from its shell.

What it obtains, by what it sends:

| The caller sends | It obtains |
|---|---|
| No header, or a header without narrowing keys | **Siblings**: every file git tracks, at any ref, in every repo listed under `[cgh] siblings` of the user's global config and of a trusted project config, through `file_at_ref` and `pattern_search`. **Shared stores**: the entries of every store the repo subscribes to in default searches, and of any existing store under `~/.cgh/shared/` by explicit name; writes to them within the existing fences (no standing instruction, no retiring another repo's note). **Memory and plans**: the memory of this checkout in the default profile, and the plans of every project of that profile |
| `claude_home` naming another profile | The memory that profile holds for this checkout (the owner derives `projects/<slug>/memory` from the checkout's own path, so not another project's), and the plans of every project of that profile |
| `memory_dir` or `plans_dir` naming any directory | The `*.md` files directly inside it: another project's memory directory in any profile, or any folder of notes of the same user |

What no header obtains: a repo that is not declared as a sibling, a file
other than `*.md` through the three directory keys, a subdirectory of the
directory named, a path outside `~/.cgh/shared/` through a store name, a file
of another user, or a write to a memory or plan file (cgh never writes them).

Every line of the table is something the same process can read from disk
without cgh.

What the scope does guarantee: an agent that uses the cgh tools it was
connected with, through the proxy its session was launched with, is served
one repository's worth of context and nothing from the neighbours. That is
the failure this design removes: context from other projects arriving
unasked in `knowledge_search`, `resume` and `context_for_task`, and sibling
code one `repo=` argument away.

Making the restriction hold against a caller that omits the header needs a
setting the owner applies to everyone, a floor. It is the other option of
review question 4.

## Delivery steps

Each step ships alone, with its tests, its documentation and its CHANGELOG
entry. Steps 2 to 4 depend only on step 1.

### Step 1: the header, and `--no-siblings`

The transport, plus the smallest restriction so that no option ships that
does nothing.

| File | Change |
|---|---|
| `cgh/core/scope.py` | new. `CallerScope`, header name, serialise, parse and validate, `current_scope()`, the default scope |
| `cgh/state/serve.py`, `cgh/__main__.py` | `--no-siblings` on `cgh serve`, passed to the bridge |
| `cgh/state/ipc.py` | `proxy_stdio_to_http` sends the header when the scope is not empty; on an `initialize` without acknowledgement restarts the owner once; after that answers each `tools/call` itself with the error of "Version skew" |
| `cgh/cli/owner_client.py` | sends the header built from the environment |
| `cgh/server/__init__.py` | the scope middleware after `_BearerAuth`, the 400 answers, the acknowledgement header |
| `cgh/analysis/refs.py` | `resolve_repo` takes the scope and refuses a sibling under `siblings: false` |
| `cgh/server/tools_query.py` | passes `current_scope()`; the `out_of_scope` answer |
| `tests/test_core/test_scope.py` | new: round trip, every malformed case |
| `tests/test_server/test_caller_scope.py` | new: the middleware and the sibling refusals |
| `tests/test_state/test_proxy_scope.py` | new: the header on the wire, the withheld response |
| `tests/test_contracts/snapshots/cli.json` | regenerated |
| `docs/CLI_REFERENCE.md`, `docs/CONFIGURATION.md`, `docs/SECURITY.md` | the option, and the paragraph saying what the scope is not |

### Step 2: shared stores

| File | Change |
|---|---|
| `cgh/state/serve.py`, `cgh/__main__.py` | `--shared-stores`, `--no-shared-stores` |
| `cgh/server/tools_knowledge.py` | `_resolved_namespaces` intersects with the scope; explicit `namespace` and shared writes check it; `scope_applied` |
| `cgh/server/tools_decision.py` | inherits the intersection; `scope_applied` |
| `cgh/analysis/context_builder.py` | knowledge hits under "See also" |
| `tests/test_state/test_knowledge_shared_stores.py`, `tests/test_server/test_caller_scope.py` | the refusals below |
| `tests/test_contracts/snapshots/cli.json` | regenerated |
| `docs/MEMORY.md`, `docs/CLI_REFERENCE.md` | the options |

### Step 3: `--no-claude-state`

| File | Change |
|---|---|
| `cgh/state/serve.py`, `cgh/__main__.py` | the option |
| `cgh/server/tools_memory.py`, `cgh/server/tools_plans.py` | refuse under `claude_state: false` |
| `cgh/server/tools_session.py`, `cgh/analysis/context_builder.py` | leave the sections out, `scope_applied` |
| `tests/test_memory_plans/test_scope_claude_state.py` | new |
| `tests/test_contracts/snapshots/cli.json` | regenerated |
| `docs/MEMORY.md`, `docs/CLI_REFERENCE.md` | the option |

### Step 4: per-caller Claude home, and the index by source

The largest step, and the only one that changes stored data.

| File | Change |
|---|---|
| `cgh/core/config.py` | `claude_home(env)` replaces the constant; `memory_dir` and `plans_dir` take a scope and follow the resolution order above |
| `cgh/core/fts.py` | the `source` column, its backfill, the filter in the four readers, the purge of dead sources |
| `cgh/claude_state/memory.py`, `cgh/claude_state/plans.py` | scan one directory given as argument, purge within its source |
| `cgh/state/watcher.py` | add a source on first sight, the cap of 8 |
| `cgh/state/ipc.py`, `cgh/cli/owner_client.py` | read `CLAUDE_CONFIG_DIR`, `CGH_MEMORY_DIR`, `CGH_PLANS_DIR` into the header |
| `cgh/server/tools_memory.py`, `tools_plans.py`, `tools_session.py`, `cgh/analysis/context_builder.py` | resolve the source from `current_scope()` |
| `tests/test_memory_plans/test_sources.py` | new: two sources in one index, the migration, the refusals below |
| `tests/test_core/test_claude_home.py` | new |
| `docs/CONFIGURATION.md`, `docs/MEMORY.md`, `docs/UPGRADING-*.md` | the variables are per caller; the index migration |

### Step 5: setup and visibility

| File | Change |
|---|---|
| `cgh/cli/commands_init.py` | `cgh init` can write the narrowing options into the MCP config it generates |
| `cgh/cli/commands_monitor.py` | `cgh status` shows the scope a `cgh serve` in this checkout would send, and the sources the index holds |
| `tests/test_cli/` | both |
| `docs/INTEGRATIONS.md` | launching a narrowed session, per agent |

## Refusal tests

The tests each step must carry. Following the house rule, an error path
asserts the state as well as the error: after a refusal, the thing that was
refused did not happen.

Transport (step 1):

1. A header that is not JSON gets HTTP 400, and the tool body never ran (the
   call log has no row for it).
2. `v: 2` gets HTTP 400.
3. An unknown key gets HTTP 400.
4. A header over 4 KB gets HTTP 400.
5. A wrong type (`"siblings": "no"`) gets HTTP 400; it is not read as true.
6. No header: `file_at_ref(repo=<sibling>)` answers exactly as before the
   change (compared against a recorded answer).
7. A request with a scope but a wrong bearer gets 401, not 400: the scope is
   never parsed for an unauthenticated caller.
8. A narrowed proxy whose `initialize` comes back without acknowledgement
   restarts the owner exactly once and sends `initialize` again.
9. Still without acknowledgement, a `tools/call` gets the JSON-RPC error and
   none of the owner's response body, while `tools/list` is forwarded.
10. A proxy started without any narrowing option, against the same owner
    that never acknowledges, forwards every answer: it sent no header and
    expects nothing.
11. A response that never arrives is not treated as a missing
    acknowledgement: the proxy restarts nothing and emits no scope error.
12. A tool function called in process, outside any request, runs under the
    default scope.

Siblings (step 1):

13. `siblings: false`, `file_at_ref(repo=<global sibling>)`: `out_of_scope`,
    and no `git` process was run in the sibling.
14. Same for `pattern_search(repo=<global sibling>)`.
15. Same with the sibling given as an absolute path instead of its name.
16. Same for a sibling declared in a trusted project config.
17. `siblings: false`, `file_at_ref(repo="", ref="origin/develop")` still
    answers: the mechanism refuses siblings, not refs.
18. `siblings: false` on a workspace with a federated subrepo:
    `pattern_search(repo=<subrepo>)` still answers.
19. A scope cannot add: `siblings: true` on a repo with no declared sibling
    makes none readable.

Shared stores (step 2):

20. `shared: []`: a default `knowledge_search` returns no hit carrying a
    shared `namespace`, the shared database file was not opened, and the
    answer has `scope_applied: ["shared"]`.
21. `shared: ["a"]` on a repo subscribed to `a` and `b`: hits from `a` only.
22. `shared: ["a"]`, `knowledge_search(namespace="b")`: `out_of_scope`.
23. `shared: []`, `knowledge_record(namespace="a")`: refused, and store `a`
    holds no new row.
24. `shared: ["c"]` where the repo does not subscribe to `c`: default
    searches read no shared store; the repo is not subscribed by the scope.
25. `shared: ["../x"]`: HTTP 400, and no path under or outside
    `~/.cgh/shared` was touched.
26. `decide` and `knowledge_diagnose` under `shared: []` cite no shared
    entry.
27. `check_action` answers the same under every scope.

Claude state (step 3):

28. `claude_state: false`: `memory_search`, `memory_list`, `plan_search`,
    `plan_list` answer `out_of_scope`.
29. `claude_state: false`, `memory_rescan`: refused, and the index row count
    is unchanged.
30. `claude_state: false`, `resume`: no plans section, no `counts.plans`,
    `scope_applied` lists `claude_state`; standing instructions and digests
    are still there.
31. `claude_state: false`, `context_for_task`: no memory or plan hit.
32. A second caller with the default scope, on the same owner, right after,
    still gets its memory hits: the refusal removed nothing from the index.

Sources (step 4):

33. Two callers with different `claude_home` on one owner: each
    `memory_search` returns only the entries of its own profile, in both
    orders of arrival.
34. Scanning source A does not remove the rows of source B (the purge that
    undoes the other session today).
35. A file deleted from source A is purged from A's rows only.
36. The owner is spawned with `CGH_MEMORY_DIR=X` in its environment and a
    caller sends a scope with `claude_home: Y`: the caller is served Y.
37. The owner is spawned with `CLAUDE_CONFIG_DIR=X` and a caller sends a
    scope without `claude_home`: it is served `~/.claude`, not X.
38. `claude_home` relative, missing, a file, or owned by another user: HTTP
    400, and nothing was indexed.
39. `claude_home` pointing at a directory holding `secret.md` at its top
    level and nothing under `projects/<slug>/memory`: `memory_search` finds
    nothing, since only the derived directories are read.
40. `--no-claude-state` with `CGH_MEMORY_DIR` set: the header carries no
    directory.
41. Migration: an index written before the change, opened after it, serves
    the same hits to a caller without scope, and every row has a `source`.
42. A ninth source is served correctly though not watched.

## Open questions for the review

Questions 1 to 4 are choices the body already follows in their recommended
form. Each gives the recommendation, the other option, and what each costs.
Questions 5 and 6 are follow-ups that no step depends on.

### 1. Does `--no-siblings` also cut federated subrepos?

**Recommended, and followed above: no.** `--no-siblings` refuses the siblings
of the global config and of the project config. A federated subrepo stays
readable by `repo=`.

- Why: subrepos are declared by the checkout's own config and every graph
  tool already fans out to them. Refusing `file_at_ref(repo=<subrepo>)` while
  `symbol_lookup` returns that subrepo's symbols would be incoherent.
- Cost: in a federating workspace, a narrowed session still reads the
  federated repos. "One repository" then means "one workspace".

**Other option: a second flag, `--no-subrepos`,** that cuts both the `repo=`
read and the federation fan-out for that caller.

- Cost: every federated read helper in `cgh/analysis/federation.py` has to
  take the scope, which touches most graph tools instead of one function.
  Answers of the same owner then differ in shape between callers (rows with
  and without `scope`). It is a step of its own, after step 1, and nothing in
  steps 1 to 5 has to change to add it later.

### 2. A refused tool: an error, or an empty answer?

**Recommended, and followed above: an explicit `out_of_scope` error** for a
tool asked for by name, and `scope_applied` on answers that only left a part
out.

- Why: an empty list reads as "nothing is known". The agent then re-derives a
  decision that exists or concludes that something is absent.
- Cost: the agent learns that something exists beyond its fence, and may
  spend a turn trying another route or telling the user. Some agents retry an
  error once.

**Other option: answer as if the source were empty** (`total: 0`, no error).

- Cost: quieter, and it leaks nothing about what exists. But the answer is
  indistinguishable from a real "no result", for the agent and for whoever
  debugs the session; a misconfigured launch (a restriction set by mistake)
  goes unnoticed for as long as nobody looks at the flags.

### 3. A narrowed proxy facing an owner that does not acknowledge the scope

**Recommended, and followed above: fail closed.** After one owner restart,
the proxy answers every `tools/call` with an error and discards the owner's
answer. See "Version skew" for what the user sees.

- Why: a restriction that silently does not apply is worse than a session
  whose cgh tools are down with a message saying how to fix it.
- Cost: a narrowed session can lose its cgh tools until the owner is
  restarted by hand, in the rare case where the automatic restart does not
  produce a current owner. One more owner restart on the path, which the
  other sessions of the checkout absorb by re-attaching. Sessions without
  narrowing are never affected.

**Other option: fail open with a warning.** The proxy forwards the answer and
logs the mismatch to stderr and to `cgh status`.

- Cost: nothing ever blocks. But the restriction is advisory during a skew,
  and the warning lands where the agent does not read and the user rarely
  looks. Given that the scope exists to stop context from leaking between
  sessions, this option gives up the one guarantee it offers.

### 4. A floor per checkout, applied even without a header

**Recommended, and followed above: no floor.** A caller that sends no header
gets the default scope; the limit is accepted and listed in "What a lying
caller obtains".

- Why: the requirement is that a missing scope mean today's behaviour, and
  the scope is not a security boundary whatever is added: the caller runs as
  the user and reads the disk.
- Cost: an agent that goes around its proxy, by `curl` or by a `cgh` command
  in its shell, reads everything the table lists. The restriction holds for
  the tools the agent was connected with, not for the agent.

**Other option: a `[scope]` table in the local `.cgh/config.toml`** (trusted
under the same rule as project siblings: local and untracked), applied by the
owner to every caller. The effective scope is the floor intersected with the
header.

- Cost: one more place where a scope is declared, and a restriction that
  applies to every session of the checkout, the user's own included, which is
  the per-owner setting this design moves away from. It fits a checkout handed
  to a single confined agent. It closes the `curl` and CLI routes, but an
  agent that can write its own `.cgh/config.toml` lifts it, so it is still
  not a boundary. It can be added as a sixth step without changing the first
  five.

### 5. Hiding tools

With `claude_state: false` the memory and plan tools could be left out of
`tools/list` for that caller, which saves their descriptions in the agent's
context. The header is on that request too, so nothing blocks it. It changes
what `[tools] expose` means (per owner today) and is left to a follow-up.

### 6. Narrowing options for one-shot CLI commands

In this design the CLI client sends its environment only. Whether `cgh query`
and the other owner-backed commands take `--no-siblings` and the like, or
read them from a variable, waits for a use.
