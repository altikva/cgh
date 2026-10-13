# Upgrading to cgh 0.19

## What changes

The MCP server is now registered as `cgh`, not `codegraph`. Agents see
its tools as `mcp__cgh__<tool>` instead of `mcp__codegraph__<tool>`
(`mcp__cgh__symbol_lookup`, `mcp__cgh__checkpoint`, ...). The Claude Code
allow rules become `mcp__cgh` and `mcp__cgh__*`.

Nothing else is renamed: the CLI is still `cgh`, the index still lives in
`.codegraph/`, `[codegraph]` in `.codegraph/config.toml` keeps its name,
and the Python import is still `codegraph`.

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

- the `"codegraph"` entry of `.mcp.json` (Claude Code, Codex, Gemini),
  `.cursor/mcp.json` and `.bob/mcp.json` becomes `"cgh"`, with the same
  settings (`env`, `timeout`, `alwaysAllow`, ...). When a file already has
  both keys, the `"cgh"` values win and `"codegraph"` is removed, so the
  client never starts two servers;
- in `.claude/settings.json` and `.claude/settings.local.json`, the
  permission rules `mcp__codegraph`, `mcp__codegraph__*` and
  `mcp__codegraph__<tool>` (in `allow`, `ask` and `deny`) become their
  `mcp__cgh` equivalents, and `"codegraph"` in `enabledMcpjsonServers` /
  `disabledMcpjsonServers` becomes `"cgh"`. Any other rule is left as is.

`cgh init` and every `cgh setup <tool>` do the same rename on their way.
`cgh setup migrate` is the one to use when you only want the rename.

Then restart the agent session. Claude Code asks once to approve the
project server `cgh`, as it does for any new name in `.mcp.json`.

`cgh doctor` reports a repo that still uses the old name, on the
`MCP server name` line, with the files to migrate. It does not fail
`cgh doctor --strict`.

## What the team updates by hand

cgh only rewrites what it wrote. These stay with the team:

1. **Settings rules outside the two cgh files.** Any `mcp__codegraph`
   rule in a settings file cgh does not manage (a managed policy, a
   template repo, `~/.claude/settings.json`).
2. **Hooks that match tool names.** A hook matcher such as
   `mcp__codegraph__scan_repo` or `mcp__codegraph__.*` no longer fires
   for the new names. Match both while sessions roll over:
   `mcp__(cgh|codegraph)__scan_repo`. `cgh doctor` names the settings
   files that still carry such a matcher; cgh never edits it.
3. **Briefs, prompts, rules and docs that cite tools by full name**
   (`mcp__codegraph__context_for_task` in a CLAUDE.md, an agent brief,
   a runbook). The rules and skills cgh installs are refreshed by
   `cgh setup <tool>`; your own files are not.
4. **Client configs outside the repo.** A server you added globally
   keeps its old name until you rename it: `~/.bob/settings/mcp.json`,
   `~/.cursor/mcp.json`, `[mcp_servers.codegraph]` in
   `~/.codex/config.toml`, `~/.gemini/settings.json`, or a
   `claude mcp add codegraph ...` at user scope (remove it and add it
   again as `cgh`).

The Claude Code plugin now declares its server as `cgh`, so plugin users
see `mcp__plugin_cgh_cgh__<tool>`.

### A script for the files in a repo

Run from the repo root after `cgh setup migrate`. It makes hook matchers
accept both names first, then rewrites every other mention. It uses perl
because `sed -i` differs between macOS and Linux. Review the diff before
committing.

```sh
# 1. Hook matchers in tracked Claude settings: accept both prefixes.
git ls-files -z -- '.claude/settings*.json' '*/.claude/settings*.json' \
  | xargs -0 -r perl -pi -e \
      's/("matcher"\s*:\s*")mcp__codegraph__/$1mcp__(cgh|codegraph)__/g' --

# 2. Every other mention, in any tracked text file: rules, briefs, docs.
git grep -lz 'mcp__codegraph' -- . ':!CHANGELOG.md' \
  | xargs -0 -r perl -pi -e 's/mcp__codegraph(?=__|\b)/mcp__cgh/g' --

# 3. What is left (expect only the dual matchers from step 1).
git grep -n 'codegraph' -- '.mcp.json' '.cursor/mcp.json' '.bob/mcp.json' \
  '.claude/settings*.json'
git diff --stat
```

On macOS, `xargs -r` is accepted and ignored; it only matters on Linux,
where it stops perl from waiting on stdin when nothing matched.
