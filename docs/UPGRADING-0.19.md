# Upgrading to cgh 0.19

## What changes

The MCP server is now registered as `cgh`, not `cgh`. Agents see
its tools as `mcp__cgh__<tool>` instead of `mcp__cgh__<tool>`
(`mcp__cgh__symbol_lookup`, `mcp__cgh__checkpoint`, ...). The Claude Code
allow rules become `mcp__cgh` and `mcp__cgh__*`.

Nothing else is renamed: the CLI is still `cgh`, the index still lives in
`.cgh/`, `[cgh]` in `.cgh/config.toml` keeps its name,
and the Python import is still `cgh`.

A repo left on the old name keeps working: the server starts under
whichever key the config gives it. Only the tool names the agent sees
differ, and so do the rules and briefs that cite them.

## The one command per repo

After upgrading cgh, run this once at the root of each repo, then commit
the files it lists:

```sh
cgh setup migrate
```

It renames, and touches nothing else:

- the `"cgh"` entry of `.mcp.json` (Claude Code, Codex, Gemini),
  `.cursor/mcp.json` and `.bob/mcp.json` becomes `"cgh"`, with the same
  settings (`env`, `timeout`, `alwaysAllow`, ...). When a file already has
  both keys, the `"cgh"` values win and `"cgh"` is removed, so the
  client never starts two servers;
- in `.claude/settings.json` and `.claude/settings.local.json`, the
  `allow` rules `mcp__cgh`, `mcp__cgh__*` and
  `mcp__cgh__<tool>` become their `mcp__cgh` equivalents, and
  `"cgh"` in `enabledMcpjsonServers` becomes `"cgh"`;
- in the same two files, every `deny` and `ask` rule on `mcp__cgh`
  gains its `mcp__cgh` twin and keeps the original, and
  `disabledMcpjsonServers` gains `"cgh"` next to `"cgh"`. A
  restriction is never dropped.

Any other rule is left as is.

`cgh init` and every `cgh setup <tool>` do the same rename on their way.
`cgh setup migrate` is the one to use when you only want the rename.

Then restart the agent session. Claude Code asks once to approve the
project server `cgh`, as it does for any new name in `.mcp.json`.

## When the rename is refused

The rename changes the tool names, so a rule or a hook written for
`mcp__cgh__<tool>` would silently stop applying. cgh checks first,
in every Claude settings layer it can read: the two project files,
`~/.claude/settings.json` and `~/.claude/settings.local.json`, and the
managed settings (`/Library/Application Support/ClaudeCode/` on macOS,
`/etc/claude-code/` on Linux, `C:\ProgramData\ClaudeCode\` on Windows).
It leaves the repo on `"cgh"`, changes nothing, and prints each
reason with its fix when it finds:

- a `deny` or `ask` rule, `disabledMcpjsonServers` or `deniedMcpServers`
  entry naming cgh in a layer it does not manage (user or managed
  settings). Fix: add the same rule for `mcp__cgh` next to it;
- a hook matcher, in any layer, that fires for `mcp__cgh__<tool>`
  but not for `mcp__cgh__<tool>`. Fix: match both, for example
  `mcp__(cgh|cgh)__scan_repo`;
- a settings layer that exists but cannot be read as JSON. Fix the file,
  since cgh cannot check what it holds.

`cgh setup migrate` exits 1 in that case. `cgh init` and `cgh setup
<tool>` keep writing the `"cgh"` key and the `mcp__cgh`
wildcards. `cgh setup migrate --force` renames anyway, with a warning
that the listed rules and hooks no longer apply.

`cgh doctor` shows the state on its `MCP server name` line. A repo not
yet migrated is reported with the command to run and does not fail
`--strict`. A refused rename is a failure, listed with its reasons, and
fails `cgh doctor --strict`.

## What the team updates by hand

cgh only rewrites what it wrote. These stay with the team:

1. **Restrictions outside the two cgh files.** `deny` or `ask` rules on
   `mcp__cgh` in a settings file cgh does not manage (managed
   policy, `~/.claude/settings.json`): add the `mcp__cgh` twin. cgh
   refuses the rename until you do.
2. **Hooks that match tool names.** A hook matcher such as
   `mcp__cgh__scan_repo` or `mcp__cgh__.*` no longer fires
   for the new names. Match both while sessions roll over:
   `mcp__(cgh|cgh)__scan_repo`. cgh refuses the rename until you do.
3. **Allow rules outside the two cgh files** (a template repo,
   `~/.claude/settings.json`): without the `mcp__cgh` twin, the agent
   prompts for each call again.
4. **Briefs, prompts, rules and docs that cite tools by full name**
   (`mcp__cgh__context_for_task` in a CLAUDE.md, an agent brief,
   a runbook). The rules and skills cgh installs are refreshed by
   `cgh setup <tool>`; your own files are not.
5. **Client configs outside the repo.** A server you added globally
   keeps its old name until you rename it: `~/.bob/settings/mcp.json`,
   `~/.cursor/mcp.json`, `[mcp_servers.cgh]` in
   `~/.codex/config.toml`, `~/.gemini/settings.json`, or a
   `claude mcp add cgh ...` at user scope (remove it and add it
   again as `cgh`).

The Claude Code plugin now declares its server as `cgh`, so plugin users
see `mcp__plugin_cgh_cgh__<tool>`.

### A script for the files in a repo

Run from the repo root. It makes hook matchers accept both names,
rewrites the mentions in briefs and docs, then runs the rename, which
gives each `deny` / `ask` rule of the two project settings files its
`mcp__cgh` twin. The last `git grep` still lists those kept originals,
on purpose. It uses perl
because `sed -i` differs between macOS and Linux. Review the diff before
committing. Settings outside the repo (`~/.claude/`, managed settings)
are yours to edit the same way.

```sh
# 1. Hook matchers in tracked Claude settings: accept both prefixes.
git ls-files -z -- '.claude/settings*.json' '*/.claude/settings*.json' \
  | xargs -0 -r perl -pi -e \
      's/("matcher"\s*:\s*")mcp__cgh__/$1mcp__(cgh|cgh)__/g' --

# 2. Briefs, rules and docs (settings files excluded: cgh setup migrate
#    handles their allow rules and twins their deny / ask rules).
git grep -lz 'mcp__cgh' -- . ':!CHANGELOG.md' ':!*settings*.json' \
  ':!*mcp.json' \
  | xargs -0 -r perl -pi -e 's/mcp__cgh(?=__|\b)/mcp__cgh/g' --

# 3. The rename itself, then what is left.
cgh setup migrate
git grep -n 'mcp__cgh' -- .
git diff --stat
```

On macOS, `xargs -r` is accepted and ignored; it only matters on Linux,
where it stops perl from waiting on stdin when nothing matched.
