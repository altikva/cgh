# Design: `decide` and `check_action`, two decision tools over the knowledge store

Status: proposed (2026-11-21)

## Context

cgh stores decisions, gotchas and standing instructions, ranks them by recorded
outcomes and hands them back on `resume` and `knowledge_search`. It never uses
them to settle anything: the agent reads the notes and decides alone. Two tools
close that gap.

- `decide(question)` looks up the precedents (this repo and the fleet) and
  answers with a recommendation, its evidence (entry ids, recorded outcomes)
  and a confidence level.
- `check_action(action)` confronts an action the agent is about to take
  ("git push origin develop", "merge PR 12", "tag v0.22.0") with the standing
  instructions and answers `allowed`, `refused` or `ask`, with the rule that
  applies, or `no_rule` when no rule speaks.

Both are deterministic. Neither calls a model: the same store and the same
input give the same answer, which is what lets a hook act on it.

What already exists and is reused, not rebuilt:

| Piece | Where |
|---|---|
| BM25 search with recall, outcome and recency factors | `knowledge_search` in `cgh/state/call_log.py` |
| Outcome scoring (`preferred`, `useful`, `contested`, `dead_end`, `corrected`, `faded`) | `cgh/state/knowledge_outcomes.py` |
| Match grade of a hit (`high`, `medium`, `low`) | `coverage` and `confidence` in `cgh/state/knowledge_recall.py` |
| Shared stores read with every hit marked `untrusted`, dedup by origin | `_read_shared_namespaces`, `_dedup_by_origin` in `cgh/server/tools_knowledge.py` |
| Standing instructions, repo store only (a shared namespace refuses that kind on write) | `knowledge_record` in `cgh/state/call_log.py` |
| PreToolUse hook on Bash, already wired by `cgh init` | `cmd_hook_precheck_bash` in `cgh/cli/commands_hooks.py` |

## Scope

In scope: the MCP and CLI contracts of the two tools, where `check_action`
reads its rules and how a rule says what it covers, how `decide` ranks
precedents, what both answer when nothing applies, and the files each
implementation ticket creates or touches.

Out of scope:

- **A risk verdict on a diff.** Judging a change by what it touches needs
  import resolution cgh does not have for every language yet. It comes later
  and will be a third tool, not a mode of these two.
- Any model call, in the tools or in the hook.
- Rules on anything other than an action string: file paths read or written,
  network destinations, tool calls other than a shell command.
- Hooks for agents other than Claude Code. The contract below is agent-neutral
  and `cgh check` works from any shell; wiring Codex, Gemini CLI or Bob is a
  later ticket.
- Enforcement as a security boundary. See "What check_action is not".

## `check_action`

### What check_action is not

It is not a sandbox. An agent that wants to get around it can: it can phrase a
command the normaliser does not recognise, or skip the tool. cgh stopped
blocking file access in 0.15.0 for that reason, and the agent's own permission
rules remain the enforcement layer. `check_action` makes the rules the user
already stated mechanical for an agent acting in good faith, so a rule is not
lost between its recording and the moment it matters. The hook can only add
friction (deny or ask); it never grants a permission the agent host would not
have granted.

### Source of the rules

Rules come from the live `standing_instruction` entries of the repo store of
the checkout the tool runs in, and from nowhere else.

- Shared stores are never a source of rules. The store already refuses a
  `standing_instruction` written to a shared namespace, because a rule read by
  every subscribed repo would be cross-repo authority injection. `check_action`
  does not read shared stores at all.
- Federated subrepos are not read either: knowledge is never federated.
- Superseded entries do not rule. Supersede is how a rule is withdrawn or
  replaced.

### How a rule declares what it covers

A standing instruction is prose. Prose cannot be matched without a model, so an
instruction that should rule carries one or more rule lines in its body:

```
cgh-rule: <verdict> <verb> [key=glob ...]
```

- `verdict` is `refuse`, `ask` or `allow`.
- `verb` is a dotted action verb from the table below, or a glob over it
  (`git.*`, `*.publish`).
- Each `key=glob` constrains one field of the action. Alternatives are
  separated by `|` (`branch=main|develop`). A key the action does not carry
  never matches.
- A rule line stands alone on its line. Everything else in the body is the
  reason, shown to the agent with the verdict.

Example, the body of one standing instruction:

```
Never push to an integration branch: every change goes through a pull
request. Releases are discussed before the tag.

cgh-rule: refuse git.push branch=main|master|develop
cgh-rule: refuse git.commit on_branch=main|master|develop
cgh-rule: ask git.tag tag=v*
cgh-rule: ask gh.release.create
cgh-rule: ask *.publish
```

Why lines in the body and not a table of their own:

- no schema migration, and `cgh knowledge promote`, `export`, `import` and the
  supersede link carry the rules with the entry, with nothing to add;
- the rule sits next to its reason, so the verdict can quote why;
- a human can read, write and review it in the note.

`knowledge_record` parses the rule lines of a `standing_instruction` at record
time and echoes them back (`rules`), with `rule_errors` for a line that does
not parse. A line that does not parse never rules; it is reported, not guessed.

A standing instruction with no rule line never produces a verdict on its own.
It can still surface: see `related` below.

### Normalising the action

The input is one string: a shell command, or a short phrase. Normalisation
turns it into a list of actions, each `{verb, fields, raw}`.

1. Split a compound command into segments on `&&`, `||`, `;`, `|` and
   newlines, with `shlex` so quoted separators stay inside their token. Drop
   leading `VAR=value` assignments, `sudo`, `env`, `command`, `exec` and
   `time`. `bash -c '<script>'` and `sh -c` recurse into the script.
2. Recognise each segment with a small table of recognisers, one per verb:

   | Verb | Recognised from | Fields |
   |---|---|---|
   | `git.push` | `git push ...` | `remote`, `branch` (the destination of each refspec, `HEAD:main` gives `main`; the current branch when none is named), `force` (`yes` for `--force`, `-f`, `--force-with-lease`, a `+` refspec), `tags` (`yes` for `--tags`), `delete` |
   | `git.commit` | `git commit ...` | `on_branch`, `amend` |
   | `git.tag` | `git tag <name>` (not `-l`, not `-d`) | `tag` |
   | `git.branch.delete` | `git branch -d/-D`, `git push --delete` | `branch` |
   | `git.reset` | `git reset --hard` | `hard` |
   | `git.rebase`, `git.merge`, `git.checkout`, `git.stash` | the matching subcommand | `branch` where one is named |
   | `gh.pr.merge` | `gh pr merge ...` | `pr`, `method`, `admin` |
   | `gh.pr.create`, `gh.pr.close` | the matching subcommand | `base`, `pr` |
   | `gh.release.create` | `gh release create ...` | `tag` |
   | `gh.issue.create` | `gh issue create ...` | `repo` |
   | `pkg.publish` | `uv publish`, `twine upload`, `npm publish`, `pnpm publish`, `cargo publish` | `tool` |
   | `tool.install` | `uv tool install/upgrade`, `pipx install/upgrade` | `tool`, `package` |
   | `proc.kill` | `pkill`, `killall` | `pattern` |

   Fields read from the repository (`on_branch`, the implicit `branch` of a
   bare `git push`) come from one `git rev-parse --abbrev-ref HEAD`, with a
   short timeout; on failure the field is absent, and a rule constraining it
   does not match. The timeout is two seconds for the tool and the CLI, and a
   parameter of the check (`git_timeout`), so the hook passes its own budget.
   A field that could not be determined is not the same as a field that does
   not apply: see "Verdict" for what an `allow` rule may do with it.
3. A segment no recogniser claims becomes `shell.<argv0>` (`shell.rm`,
   `shell.curl`) with one field, `args`, the rest of the command line. Rules
   can still target it: `cgh-rule: refuse shell.rm args=*-rf*`.
4. A phrase that is not a command is matched against a short alias list, in
   French and English, anchored on the first words: "merge PR 12" and
   "fusionner la PR 12" give `gh.pr.merge pr=12`, "tag v0.22.0" gives
   `git.tag tag=v0.22.0`, "push to develop" gives `git.push branch=develop`,
   "release v0.22.0" gives `gh.release.create tag=v0.22.0`. A phrase that
   matches no alias normalises to nothing.

The recogniser table and the alias list are data in one module, so adding a
verb is one entry and one test.

### Verdict

For each normalised action, collect the rule lines whose verb glob and every
field constraint match. Then:

- any matching `refuse` gives `refused`;
- else any matching `ask` gives `ask`;
- else any matching `allow` gives `allowed`;
- else no rule applies: `no_rule`.

`allowed` is never a default. It requires a `cgh-rule: allow` line that
matches the action. Silence of the store is not consent, so it has its own
verdict.

Over a compound command the strictest verdict of its segments wins, in the
order `refused`, `ask`, `no_rule`, `allowed`, and the answer names the
segment. A command is `allowed` only when every one of its segments is.

An `allow` rule never overrides a `refuse` or an `ask`, whatever its
specificity or age. To carve an exception out of a refusal, the user narrows
or supersedes the refusing rule. This keeps the precedence readable and keeps
an agent from allowing itself past a refusal by recording a note.

An `allow` rule does not decide an action one of whose fields could not be
determined, when a `refuse` or `ask` rule constrains that field and matches
the rest of the action. The branch of `git push --all`, of a matching refspec,
of a push or commit from a directory outside the checkout, or of a git that
does not answer in time is undetermined. With `refuse git.push
branch=main|develop` and a broad `allow git.push`, such a push answers
`no_rule`, not `allowed`: the refusal does not match, since the field is
absent, but the allow rule cannot clear what the refusal might have covered.
A field that simply does not apply (no `force` on a plain push) is not
undetermined and withholds nothing.

An `allow` rule recorded with the `session_id` of the calling session does not
count for that session: its match is reported in `ignored` and the verdict
falls through as if it were absent, to `no_rule` when nothing else matches. A session does not write its own
permission slip.

### When nothing applies

The verdict is `no_rule`, with `rule: null`. It means "the store has nothing
to say": the agent falls back on what it would have done without the tool,
which for anything hard to undo is to ask the user. Two cases lead there, told
apart by `recognised`:

- `recognised: true`: the action was normalised to at least one verb and no
  rule line matched it;
- `recognised: false`: the input could not be normalised at all (a phrase no
  alias matches, an empty or unparseable string). No rule was even tried.
  This is never `allowed` either: an action cgh cannot read is not an action
  cgh cleared.

A shell command always normalises, at worst to `shell.<argv0>`, so
`recognised: false` only concerns phrases and malformed input.

When the action was recognised, the tool then
runs the action's words (verb parts and field values) through
`knowledge_search` restricted to `kind="standing_instruction"` on the repo
store and returns up to three hits as `related`: instructions that mention the
subject but declare no rule for it. They are context for the question the
agent puts to the user, not a verdict.

`ask` and `no_rule` are different answers: `ask` means a rule of the user says
to ask, `no_rule` means nobody ruled. The hook relies on the difference.

### MCP contract

```
check_action(action: str, session_id: str = "") -> JSON
```

| Output field | Type | Meaning |
|---|---|---|
| `action` | string | the input, verbatim |
| `verdict` | `allowed` \| `refused` \| `ask` \| `no_rule` | the answer |
| `recognised` | bool | whether the input normalised to at least one action |
| `rule` | object or null | the deciding rule: `entry_id`, `title`, `line` (the rule line), `reason` (the body without its rule lines, clipped), `recorded` (UTC) |
| `actions` | list | each normalised action: `verb`, `fields`, `raw`, `verdict`, `rule_entry_id` |
| `others` | list | other matching rules that did not decide, as `{entry_id, line, verdict}` |
| `ignored` | list | `allow` rules skipped because the calling session recorded them |
| `related` | list | only with `no_rule`: `{id, title, confidence}` of standing instructions that mention the subject |
| `next` | string | one line on what to do: proceed, do not proceed and why, ask the user, or "no rule covers this, use your own judgment and ask before anything hard to undo" |

The tool reads `_srv._root` at call time, like every tool of `cgh.server`. It
reads neither the graph nor the code FTS, so it is not federated. It is
read-only: it records no outcome and no call beyond the usual tool log.

`check_action` joins `CORE_TOOLS`: a tool that must be called before acting is
useless if the client has deferred it. `decide` stays in the full set.

### CLI contract

```
cgh check "<action>" [--root PATH] [--json] [--session-id ID]
```

Text output is one verdict line, the rule line and its entry id, then the
reason. `--json` prints the MCP payload. The command opens the knowledge store
directly, as `cgh papercut` does, and needs no running owner.

| Exit code | Meaning |
|---|---|
| 0 | `allowed`, and only `allowed` |
| 2 | `refused` |
| 3 | `ask` |
| 4 | `no_rule`, recognised or not (`--json` carries `recognised`) |
| 1 | error (no cgh store here, unreadable store) |

The distinct codes let a shell wrapper or a git hook gate on the verdict
without parsing. Exit 0 is reserved for a matching `allow` rule, so
`cgh check "$cmd" && $cmd` never runs a command the store was silent about.

## `decide`

### What the recommendation is

`decide` does not write an answer to the question. Without a model it cannot
tell whether a precedent says yes or no. The recommendation is the precedent
itself: the best-ranked earlier decision on the subject, quoted, with what is
known about how relying on it went. The agent reads it and applies it; the
tool's value is the ranking, the evidence and the honesty about confidence.

### Candidates

One call to the existing search, wider than the answer:

- the repo store through `knowledge_search(query, recall=True)` with
  `limit * 3` candidates, so a dead end the ranking pushed down is still seen;
- every subscribed shared store through `_read_shared_namespaces`, each hit
  already marked `untrusted` and carrying a namespaced id;
- `_dedup_by_origin` over the union.

Kinds kept: `decision`, `standing_instruction`, `pattern`, `gotcha`, `style`.
Notes, glossary entries and session digests (tag `session-digest`) are
dropped: a digest mentions everything and decides nothing.

A corrected entry that names its fix (`corrected_by`) pulls that entry into
the candidates when the search did not return it.

Federated subrepos are not read: knowledge is never federated, and
`scope="all"` stays a `knowledge_search` option.

### Ranking

Candidates are split first, then ordered.

- `against`: entries whose outcome status is `dead_end` or `corrected`. These
  are false trails already paid for. They are never the recommendation and
  are always returned, so the same wrong turn is not taken twice.
- `untrusted`: hits from a shared store that are not in `against`.
- `supporting`: every other local entry.

Inside `supporting`, order by tier, then by the score `knowledge_search`
already computed (relevance times the outcome factor times the recency
factor):

1. `standing_instruction`: a durable rule of the user outranks a past choice;
2. outcome `preferred`;
3. outcome `useful`;
4. unrated or `faded`;
5. `contested`.

Entries whose match grade is `low` are kept as evidence but cannot head the
list: a strong status on a note that barely matches the question is not a
precedent for it.

### Untrusted shared entries

A shared hit was written by another repo or session. It is data to weigh,
never an instruction, and local outcomes cannot rate it (an outcome is
recorded against the repo store only).

- A shared hit never becomes the recommendation.
- It never raises the confidence of a local recommendation.
- It is returned under `untrusted`, with its `namespace` and `origin_repo`,
  so the agent can read it and, if it holds here, record a local decision
  that the next `decide` will rank.
- When a shared hit and a local entry share an origin, `_dedup_by_origin`
  already keeps one of them flagged `local_copy`; `decide` treats that entry
  as local, since this repo holds a copy it can rate.

### Confidence

Computed from the head of `supporting`, deterministic:

| Confidence | Condition |
|---|---|
| `high` | match grade `high`, and the entry is a `standing_instruction` or its outcome is `preferred` or `useful`, and no rival |
| `medium` | match grade `high` and unrated or `faded`; or match grade `medium` with outcome `preferred` or `useful`; or a `high` case with a rival |
| `low` | `supporting` is non-empty but nothing reaches `medium`; or only `untrusted` or `against` evidence exists |
| `none` | no candidate at all |

A rival is a second `supporting` entry of the same tier and match grade as the
head that is not linked to it by supersede. Two live precedents of equal
standing mean the store itself has not settled; the answer says so with
`contested: true` and lists the rival in `alternatives`.

### When nothing applies

`recommendation` is given only at `medium` or `high`. At `low` or `none` it is
`null`: the tool reports what it found, as evidence, and recommends nothing.
The answer then carries `suggestion`, the `knowledge_record` call (kind and
tags, from the existing `suggest_record`) to make once the question is
settled, so the next session has a precedent.

### MCP contract

```
decide(question: str, namespace: str = "", limit: int = 5, session_id: str = "") -> JSON
```

`namespace` behaves as in `knowledge_search`: empty reads the repo and the
subscribed shared stores, a local facet (`repo`, `ticket:<id>`) reads this
repo only, a shared name reads that store only (and can then return no
recommendation, by the rule above).

| Output field | Type | Meaning |
|---|---|---|
| `question` | string | the input, verbatim |
| `recommendation` | object or null | `entry_id`, `title`, `kind`, `statement` (the body, clipped near 600 characters), `recorded` (UTC), `outcome` (the usual summary) when rated |
| `confidence` | `high` \| `medium` \| `low` \| `none` | as defined above |
| `basis` | string | one line saying why this confidence: match grade, status, rival or not |
| `contested` | bool | present and true when a rival exists |
| `alternatives` | list | rivals, same shape as `recommendation` without `statement` |
| `evidence.supporting` | list | `{id, title, kind, match, outcome?}` in rank order, the recommendation first |
| `evidence.against` | list | dead ends and corrected entries, with `outcome.last_note` and `corrected_by` |
| `evidence.untrusted` | list | shared hits: `{id, title, kind, match, namespace, origin_repo}` |
| `suggestion` | object | only when `recommendation` is null |
| `next` | string | at `medium` or `high`: rate the precedent after acting, with `knowledge_outcome(entry_id=..., verdict=...)` |

The answer is fitted to the search answer budget (`SEARCH_TARGET_BYTES`):
evidence briefs are dropped from the tail first, `statement` is clipped last.
Read any entry whole with `knowledge_list(entry_id=<id>)`.

`decide` is read-only. It does not record an outcome on the caller's behalf:
whether the precedent held is only known after the agent acted, and `next`
says how to report it.

### CLI contract

```
cgh decide "<question>" [--root PATH] [--namespace NAME] [--limit N] [--json]
```

Text output: the recommendation (or "no recommendation"), the confidence and
its basis, then the evidence in three short lists. `--json` prints the MCP
payload. Exit code 0 whenever the lookup ran, whatever the confidence; 1 on
error. Like `cgh check`, it opens the store directly.

## The hook

`check_action` only helps if it runs before the action. Claude Code already
calls `cgh _hook_precheck_bash` before every Bash command in a repo set up by
`cgh init`. The check goes there, first, before the search hints: no second
process per command, and no repo needs a new `cgh init` to get it.

Behaviour, given the verdict for the command about to run:

| Verdict | Hook output |
|---|---|
| `refused` | `permissionDecision: "deny"`, the reason naming the rule line, its entry id and its reason text |
| `ask` | `permissionDecision: "ask"`, same reason: the host asks the user |
| `no_rule` | nothing: the command runs under the host's own permission rules |
| `allowed` | nothing: the hook never emits `allow`, so it cannot bypass a host prompt |

`no_rule` stays silent on purpose: most commands are ones nobody ruled on, and
a prompt before every `ls` would make the hook unusable.

Cost: the hook already starts a Python process and already opens the store.
The check adds one query for live standing instructions whose body contains
`cgh-rule:`, and returns at once when there is none. A store with no rule line
behaves exactly as today.

Mode, under `[hooks]` in the repo's `.cgh/config.toml` then in
`~/.cgh/config.toml`, with `CGH_HOOK_CHECK_ACTION` overriding both:

- `check_action = "enforce"`: the table above.
- `check_action = "advise"`: no permission decision; the verdict and the rule
  go to the model as `additionalContext`.
- `check_action = "off"`: the hook does not check.

Which of `enforce` and `advise` is the default is not settled here: see the
open questions. Both are specified so that only the default changes.

### The hook never blocks by failing

A deny or an ask is only ever the product of a rule line that was read,
parsed and matched. Every other path ends in silence, exit 0, and the command
runs:

- The check does not depend on the owner. It reads the knowledge store file
  directly, so an owner that is down, wedged, unreachable or restarting
  changes nothing. If a later version routes the check through the owner, the
  same budget applies to that call and a missed deadline is silence.
- Time budget: 200 ms of wall clock for the whole check, measured from the
  moment the payload is parsed. Inside it, the store is opened read-only with
  a 50 ms busy timeout and the one `git rev-parse` runs with a 100 ms timeout.
  A deadline missed anywhere abandons the check. A `git rev-parse` that times
  out alone only drops the fields it feeds, as described above; it cannot
  produce a deny.
- No store, a locked or corrupt store, an unparseable payload, an unknown mode
  value, any exception in the normaliser or the matcher: silence.

### The repository a command aims at

The store read is not only the one of the session's directory. A session
started outside any repository, or in a coordination checkout, acts on a
worktree with `git -C <dir> push` or `cd <dir> && git push`: the rules that
matter are the worktree's. So the hook reads:

1. the store of the cgh root of the session's directory, when there is one,
   and checks the whole command against it, as before;
2. the store of each other cgh root the command aims at, and checks against
   it only the actions that run inside that root.

The normaliser gives each action the directory it runs in: where the line
stands after `cd` or `pushd`, or the `-C` directory of `git`. `gh --repo
owner/name` is placed in a checkout only when the user declared one: an
absolute `[cgh] siblings` entry of the global config whose git config has
that repository as a remote. Files are read; git is not run and the disk is
not searched.

The two sets of rules add up, and the strictest decision wins (deny, then
ask, then advise). Replacing the session's rules by the aimed repository's
was rejected: `cd <another repo> &&` would then be a way out of them.

Which branch a rule sees follows the same directory, with one asymmetry, so
that nothing is loosened on a guess:

- for the aimed repository's rules, the branch is read in the directory the
  action points at, when it is inside that repository. This is the one place
  where git runs in a directory the command names outside the session's
  checkout, and only inside a cgh root;
- for the session's rules, the session's branch stops being used only when
  the action certainly runs elsewhere: a literal `git -C <dir>`, or a literal
  `cd <dir>` that every later segment follows through `&&`. After `;`, `||`,
  a pipe or a parenthesis, after `cd -` or `popd`, or with a path the shell
  would expand, the action is checked with the session's branch as before,
  and also by the aimed repository when the guess points into one.

Failures stay silent and stay local. Each repository is read on its own: a
store that is missing, locked or corrupt, a config that cannot be read, an
exception, adds no decision and removes none from the other repositories. The
200 ms budget covers all of them: the session's repository is read first, at
most three aimed repositories follow, each git call gets what is left of the
budget and never more than 100 ms, and a repository not reached in time is
skipped. A mode of `off` in one repository turns off that repository's rules
only.

The hook ticket proves it with tests that each assert exit 0 and an empty
stdout, on a store that holds a `refuse` rule matching the command (so a
working check would have denied):

1. the store held under an exclusive lock by another connection for longer
   than the budget; the hook returns within 1 s;
2. a `git` on `PATH` that sleeps 5 s, with a rule that constrains
   `on_branch`; the hook returns within 1 s (a separate test shows a rule
   that needs no repository field still denies under the same slow `git`);
3. a stale owner pidfile and a dead owner socket in the state directory, no
   owner running: the verdict is the same as with a live owner;
4. a store file truncated to garbage;
5. the matcher patched to raise.

## Contract snapshots

`tests/test_contracts/test_public_contracts.py` snapshots the MCP tool schemas
and the CLI parser tree. Each implementation ticket regenerates and commits
them in its own pull request
(`CGH_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contracts -q`):

- `tests/test_contracts/snapshots/mcp_tools.json`: one new entry per tool,
  `check_action` (`action` required, `session_id`) and `decide` (`question`
  required, `namespace`, `limit`, `session_id`).
- `tests/test_contracts/snapshots/cli.json`: the `check` and `decide` verbs
  and their flags.

The output shapes are not in the snapshots. Each ticket pins them with a test
that asserts the exact key set of an answer, for the matched, `no_rule` and
empty-store cases.

## Files per ticket

### Ticket: `check_action`

| File | Change |
|---|---|
| `cgh/state/action_rules.py` | new. Pure functions, no SQL and no MCP: parse rule lines out of a body, normalise an action string (segments, recognisers, aliases), match, fold verdicts |
| `cgh/state/call_log.py` | a reader for live standing instructions carrying rule lines (repo store only) |
| `cgh/server/tools_decision.py` | new. Registers `check_action` |
| `cgh/server/__init__.py` | register the new tool module |
| `cgh/server/tools_knowledge.py` | `knowledge_record` echoes `rules` and `rule_errors` for a `standing_instruction` |
| `cgh/core/tool_exposure.py` | `check_action` in `CORE_TOOLS` |
| `cgh/cli/commands_decision.py` | new. `cgh check`, text and `--json`, the exit codes |
| `cgh/__main__.py` | parser, dispatch entry and help line for `check` |
| `tests/test_state/test_action_rules.py` | new: rule parsing, each recogniser, refspecs, compound commands, aliases, precedence, the same-session `allow` |
| `tests/test_server/test_check_action.py` | new: answer shape, `no_rule` with `related`, `recognised` false, an empty store never answers `allowed`, superseded rule ignored, shared store never read |
| `tests/test_cli/test_check_cli.py` | new: the five exit codes and `--json` |
| `tests/test_contracts/snapshots/mcp_tools.json`, `cli.json` | regenerated |
| `docs/MCP_TOOLS.md`, `docs/CLI_REFERENCE.md`, `docs/MEMORY.md` | the tool, the verb, the rule line syntax |
| `cgh/skills/cgh-record-knowledge/SKILL.md` | how to write a rule line when recording a standing instruction |
| `CHANGELOG.md` | entry under Unreleased |

### Ticket: `decide`

| File | Change |
|---|---|
| `cgh/state/decision.py` | new. Pure functions: split candidates, tier order, rival detection, confidence and `basis` |
| `cgh/server/tools_decision.py` | registers `decide`; reuses `_read_shared_namespaces`, `_dedup_by_origin` and `_fit_search_answer` from `tools_knowledge.py` |
| `cgh/cli/commands_decision.py` | `cgh decide` |
| `cgh/__main__.py` | parser, dispatch entry and help line for `decide` |
| `tests/test_state/test_decision.py` | new: tiers, the low-match rule, rivals, each confidence row, `none` |
| `tests/test_server/test_decide.py` | new: answer shape, a shared hit never recommended, dead ends under `against`, `corrected_by` pulled in, digests dropped |
| `tests/test_cli/test_decide_cli.py` | new |
| `tests/test_contracts/snapshots/mcp_tools.json`, `cli.json` | regenerated |
| `docs/MCP_TOOLS.md`, `docs/CLI_REFERENCE.md`, `docs/MEMORY.md` | the tool and the verb |
| `cgh/skills/cgh-use-memory/SKILL.md` | when to call `decide` instead of `knowledge_search` |
| `CHANGELOG.md` | entry under Unreleased |

The two tickets are independent. Whichever lands second adds to
`tools_decision.py` and `commands_decision.py` instead of creating them.

### Ticket: hook wiring

Depends on the `check_action` ticket.

| File | Change |
|---|---|
| `cgh/cli/hook_check.py` | new. Mode resolution (environment, then `[hooks]` in the repo config, then the global one, as `hook_answers.enabled` does), payload to verdict, the deny, ask and advise outputs |
| `cgh/cli/commands_hooks.py` | `cmd_hook_precheck_bash` calls the check first; `_emit_deny` reused, an `_emit_ask` added next to it |
| `cgh/core/config.py` | the `check_action` key of `[hooks]` in the commented config template |
| `tests/test_cli/test_hook_check_action.py` | new: each row of the hook table, each mode, a store without rule lines stays silent, and the five failure tests of "The hook never blocks by failing" |
| `docs/CONFIGURATION.md`, `docs/INTEGRATIONS.md` | the mode key and what the hook does |
| `CHANGELOG.md` | entry under Unreleased |

`cgh/cli/commands_init.py` is not touched: the hook entry it writes already
exists.

## Open questions

1. **Default hook mode.** `enforce` or `advise`. `enforce` is what stops a
   push to an integration branch, and nothing is denied until a user writes a
   `refuse` rule line. `advise` keeps the position cgh took in 0.15.0, when
   the file guard was removed, of never blocking. This document does not
   choose. The maintainer decides before the hook ticket starts; both modes
   are specified, so only the default changes.
2. **Seeding rules.** Existing standing instructions are prose and carry no
   rule line, so the day the tools ship no store rules on anything. Whether
   `cgh memory review` should list standing instructions without a rule line,
   and whether `cgh init` should offer a starter set (integration branches,
   tags, publish), is left to a follow-up.
3. **Rules that travel with the repository.** A store is per checkout and not
   committed. A team that wants the same rules for everyone would need a
   committed rule file; that reopens the question of trusting committed
   configuration and is not attempted here.
