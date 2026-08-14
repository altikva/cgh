# cgh CLI Reference

Reference for the core CLI verbs. The single entry point is `cgh`;
plugin verbs (`vision`, `summarize`, `insights`, `classify`, `pii`,
`bug`) are documented in their plugin's README.

Global flag available on all commands:

```
--root <DIR>    Target a different project root (default: current directory)
```

Shared options on artifact-emitting verbs (`vision`, `impact`, more
adopting over time):

```
--out <PATH>         Also write the result to a file (stdout keeps it too);
                     without it, interactive runs print a one-line tip.
--format md|json     md is the human default; json is the machine shape,
                     the same dicts the SDK returns.
```

---

## Getting Started

### `init`

Interactive wizard that initializes codegraph in a project. Detects AI tools, installs MCP server configs and hooks, scans for parseable files, and optionally runs the first index.

```
cgh init [--yes | -y] [--secure] [--no-children] [--tools LIST] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Accept all defaults, skip interactive prompts |
| `--secure` | Enable secure mode (`mode = "secure"`) without prompting |
| `--no-children` | Don't initialize / refresh federated subrepos |
| `--tools` | Comma-separated tools to wire regardless of detection (`claude,cursor,codex,gemini,bob`). For a fresh repo where cgh cannot detect the tool yet |

The interactive selection is offered even when no tool is detected (a
fresh repo can still wire one by hand); `--tools` forces the choice for a
scripted or empty-repo bootstrap.

**What it does:**

1. Creates `.codegraph/` directory and `config.toml`
2. Generates MCP auth key (`.codegraph/auth.key`)
3. Adds `.codegraph/` and `.codegraph/auth.key` to `.gitignore`
4. Detects installed AI tools (Claude Code, Cursor, Codex, Gemini, IBM Bob)
5. Offers secure mode (guards fail closed, egress allowlist); assist stays the default
5. Prompts (multi-select) which tools to install MCP configs for: pick one or many
6. For selected tools: writes MCP config, installs the bundled skills, and (optional) writes the codegraph usage guidelines to the agent's rules (CLAUDE.md / AGENTS.md / GEMINI.md / `.cursor/rules/` / `.bob/rules/`)
7. Offers Claude-specific auto-accept for MCP tool calls
8. Counts parseable files by language
9. Optionally runs `cgh index`

### `reset`

Nuke the graph + FTS DBs, kill the running owner, and re-index from scratch. Useful after schema migrations or when the graph gets into a weird state.

```
cgh reset [--yes | -y] [--drop-extra-dirs] [--no-reindex] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Skip confirmation |
| `--drop-extra-dirs` | Also remove `extra_dirs` from `config.toml` |
| `--no-reindex` | Don't re-index after cleaning (leave empty DB) |

### `tail`

Live view of scan/watcher activity. Works even when the MCP owner holds the graph write lock.

```
cgh tail [--follow | -f] [--limit N] [--root DIR]
```

### `index --method`

Choose the file-discovery strategy:

```
cgh index --method {auto,git_ls_files,os_walk,find,git_diff,incremental}
```

- `auto`: git_ls_files, falls back to os_walk (default)
- `git_ls_files`: force git, respects `.gitignore`
- `os_walk`: Python walk, respects `_IGNORE_DIRS` + `.cghignore`
- `find`: GNU `find -type f`, fast on big repos
- `git_diff`: only files changed since the last scan
- `incremental`: only files whose git blob SHA drifted

### `stats --live`

Refresh stats every 500 ms (Rich Live). Ctrl-C to stop.

**Example:**

```bash
cgh init
cgh init --yes    # CI-friendly, no prompts
cgh init --secure # harden the repo from the start
```

---

### `parsers`

List all registered language parsers with their file extensions and extracted symbols.

```
cgh parsers
```

No flags. Output shows language name, supported extensions, what each parser extracts, and a short description.

---

### `setup`

Generate integration files for a specific AI tool without the full interactive wizard.

```
cgh setup <target> [--root DIR]
```

| Argument | Values |
|----------|--------|
| `target` | `claude`, `cursor`, `codex`, `gemini`, `bob`, `all` |

**Example:**

```bash
cgh setup claude     # writes .mcp.json
cgh setup cursor     # writes .cursor/mcp.json
cgh setup bob        # writes .bob/mcp.json + .bob/skills/
cgh setup all        # writes configs for all tools
```

---

### `index`

Build or rebuild the full code graph. Discovers files via `git ls-files` (falls back to `os.walk` in non-git dirs). Parses every supported file and stores nodes/edges in the graph DB (DuckDB by default, Kuzu via `CGH_DB=kuzu`) and BM25 FTS index.

```
cgh index [--verbose | -v] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--verbose`, `-v` | Show per-file parsing details |

**Example:**

```bash
cgh index
cgh index --verbose
```

The command shows a progress bar with file count, current file, and elapsed time. On completion, prints an Index Summary table.

---

### `watch`

Run a full index then watch the filesystem for changes indefinitely. Uses watchdog with debounced incremental re-indexing.

```
cgh watch [--verbose | -v] [--root DIR]
```

Press Ctrl-C to stop.

---

### `serve`

Start the MCP server over stdio transport. This is the command that AI tools invoke via their MCP config.

```
cgh serve [--watch] [--reindex] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--watch` | Enable live file watcher alongside the MCP server |
| `--reindex` | Rebuild the graph before accepting MCP connections |

**Example (in `.mcp.json`):**

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "codegraph",
      "args": ["serve", "--root", ".", "--watch", "--reindex"]
    }
  }
}
```

---

## Query

### `search`

Fuzzy search symbols (functions, classes, doc sections) by substring match.

```
cgh search <query> [--limit N | -n N] [--json] [--root DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit`, `-n` | 20 | Maximum results |
| `--json` | | Output as JSON instead of a table |

**Example:**

```bash
cgh search "Handler"
cgh search "receipt" --limit 5
cgh search "validate" --json
```

---

### `lookup`

Find the exact definition of a named symbol (function, class, TF resource, or doc section).

```
cgh lookup <name> [--root DIR]
```

Returns symbol kind, name, file path, and line range. If the name matches a Markdown section title (substring match), doc sections are included.

**Example:**

```bash
cgh lookup verify_token
cgh lookup BaseHandler
cgh lookup "Multi-tenancy"    # matches doc sections
```

---

### `callers`

Show all functions that call a given function, displayed as a tree.

```
cgh callers <fn_name> [--root DIR]
```

**Example:**

```bash
cgh callers verify_token
```

---

### `callees`

Show all functions called by a given function, displayed as a tree.

```
cgh callees <fn_name> [--root DIR]
```

**Example:**

```bash
cgh callees get_current_user
```

---

### `outline`

Display the heading structure of a Markdown file as a hierarchical tree.

```
cgh outline <file> [--root DIR]
```

Accepts relative or absolute paths. The file must be indexed.

**Example:**

```bash
cgh outline CLAUDE.md
cgh outline docs/ARCHITECTURE.md
```

---

### `graph`

Generate and display interactive Mermaid graph visualizations in the browser.

```
cgh graph [scope] [--symbol NAME | -s NAME] [--file PATH | -f PATH]
          [--max-nodes N | -n N] [--mermaid] [--html FILE] [--root DIR]
```

| Argument/Flag | Default | Description |
|---------------|---------|-------------|
| `scope` | `overview` | What to visualize: `overview`, `imports`, `calls`, `classes`, `docs` |
| `--symbol`, `-s` | | Filter to a symbol (for `calls`, `classes`) |
| `--file`, `-f` | | Filter to a file (for `imports`, `docs`) |
| `--max-nodes`, `-n` | 40 | Maximum nodes in the diagram |
| `--mermaid` | | Output raw Mermaid to stdout instead of opening browser |
| `--html FILE` | | Write HTML to a file instead of opening browser |

**Examples:**

```bash
cgh graph                           # overview
cgh graph imports                   # all file imports
cgh graph imports -f auth.py        # imports of auth.py only
cgh graph calls -s verify_token     # call graph around verify_token
cgh graph classes                   # class hierarchy
cgh graph docs                      # documentation structure
cgh graph calls --mermaid           # raw Mermaid output
cgh graph imports --html graph.html # save to file
```

---

## Monitor

### `stats`

Display comprehensive statistics: graph nodes, edges, MCP call stats, FTS index, and storage sizes.

```
cgh stats [--json] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON |

**Example:**

```bash
cgh stats
cgh stats --json
```

---

### `logs`

View MCP tool call history with timestamps, latency, result sizes, and error status.

```
cgh logs [--tool NAME | -t NAME] [--errors | -e] [--limit N | -n N]
         [--json] [--clear] [--root DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--tool`, `-t` | | Filter by tool name |
| `--errors`, `-e` | | Show only failed calls |
| `--limit`, `-n` | 50 | Max entries to show |
| `--json` | | Output as JSON |
| `--clear` | | Delete all log entries |

**Example:**

```bash
cgh logs
cgh logs --tool symbol_lookup
cgh logs --errors --limit 10
cgh logs --clear
```

---

### `history`

Show recent MCP activity grouped by day, with per-day call counts and top tools.

```
cgh history [--days N | -d N] [--root DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days`, `-d` | 7 | Number of days to show |

**Example:**

```bash
cgh history
cgh history --days 30
```

---

### `diff`

Show files changed since a git ref, categorized into parseable and non-parseable. Also shows untracked parseable files not yet indexed.

```
cgh diff [--since REF] [--root DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--since` | `HEAD` | Git ref to diff against |

**Example:**

```bash
cgh diff
cgh diff --since HEAD~5
cgh diff --since main
```

---

### `files`

List the indexed files (optionally filtered by a path substring), or check
whether a specific file is indexed and, if not, why it was skipped.

```
cgh files [PATTERN] [--check PATH] [--limit N] [--root DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `PATTERN` | (all) | Only list indexed files whose path contains this |
| `--check` | | Report whether PATH is indexed; if not, the reason (no parser for the suffix, over the `max_file_size_kb` cap, an ignore rule) |
| `--limit` | `200` | Max files to list |

**Example:**

```bash
cgh files                       # every indexed file
cgh files .xlsx                 # indexed files whose path has ".xlsx"
cgh files --check report.xlsx   # indexed? if not, why
```

---

## Maintenance

### `doctor`

Run a health check that verifies all codegraph components are operational.

```
cgh doctor [--root DIR]
```

Checks performed:

1. `.codegraph/` directory exists
2. Graph DB (`graph.duckdb` or `graph.db`) is accessible
3. `fts.db` (SQLite FTS5) is accessible
4. `call_log.db` is accessible
5. `config.toml` is valid TOML
6. Parsers load successfully
7. `git` is available in PATH
8. `.cghignore` exists (optional)
9. `fastmcp` is importable (MCP server dependency)

---

### `compact`

Vacuum SQLite databases (`fts.db`, `call_log.db`) to reclaim space. Shows before/after sizes. The graph DB (`graph.duckdb` or `graph.db`) is displayed for reference but is not vacuumable via this command.

```
cgh compact [--root DIR]
```

---

### `migrate-to-duckdb`

Re-index a repo currently on the Kuzu backend into DuckDB, verify counts match, and optionally delete `graph.db`. Safe to run mid-flight: keeps the old DB around until you confirm.

```
cgh migrate-to-duckdb [--yes | -y] [--keep-kuzu] [--force] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Skip the "delete graph.db?" prompt and delete on success |
| `--keep-kuzu` | Never delete `graph.db`, even on exact count match |
| `--force` | Overwrite an existing `graph.duckdb` before re-indexing |

The verifier compares per-label node + per-type edge counts between the two backends and classifies the diff:

- **matched**: exact counts; swap proceeds.
- **stale_kuzu**: every diff is explained by a fix shipped after the Kuzu DB was written (`IMPORTS` going from `0` to N, or any metric where DuckDB ≤ Kuzu, i.e. ghost rows from deleted files). DuckDB is accepted as canonical and the swap proceeds.
- **mismatched**: DuckDB gained rows that aren't explained by a known post-fix signature. Both files are kept and the command exits non-zero so you can inspect manually.

`cgh init` runs this automatically when it detects only `graph.db` is present.

---

## Advanced

### `add-dir`

Manage extra directories included in the graph. Useful for multi-repo setups (e.g., indexing both `ondonne-api` and `ondonne-frontend` together).

```
cgh add-dir [action] [paths...] [--root DIR]
```

| Action | Description |
|--------|-------------|
| `list` (default) | Show configured extra directories |
| `add <paths...>` | Add directory paths to the config |
| `remove <paths...>` | Remove directory paths from the config |

**Example:**

```bash
cgh add-dir list
cgh add-dir add ../ondonne-frontend ../ondonne-infra
cgh add-dir remove ../ondonne-infra
```

After adding, run `cgh index` to include the new directories.

---

### `force-index`

Index specific files or directories that are normally excluded by `.gitignore` or `.git/info/exclude`. Always requires confirmation (unless `--yes`).

```
cgh force-index <paths...> [--yes | -y] [--verbose | -v] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--verbose`, `-v` | Show details during indexing |

**Example:**

```bash
cgh force-index build/output.py docs/generated/
cgh force-index build/output.py --yes
```

### `plugins`

List installed cgh plugins with their status. Plugins are discovered
through the `cgh` entry point group; anything installed with pip that
exposes it shows up here.

```
cgh plugins [--json] [--root DIR]
```

| Column | Meaning |
|--------|---------|
| `status` | `active`, `disabled` (via `[plugins]` config), `incompatible` (API version mismatch), `broken` (import or registration failed), `duplicate` |
| `api` | The `CGH_PLUGIN_API` version the plugin declares |
| `surfaces` | What it registered: `parsers`, `scanners`, `mcp`, `cli`, `extensions` |
| `note` | The reason for any non-active status |

**Example:**

```bash
cgh plugins
cgh plugins --json      # machine-readable, for scripts
```

### `findings`

Query the finding store: what scanner plugins know about each file
(`pii.email`, `secret.aws_key`, `confidential`, `summary`, ...).
Federated over subrepos with a `scope` column.

```
cgh findings [FILE] [--key PREFIX] [--severity info|warn|block] [--limit N] [--json] [--root DIR]
```

**Example:**

```bash
cgh findings --key pii.            # every PII finding in the repo
cgh findings src/billing.py       # everything known about one file
cgh findings --severity block     # what the gates would stop
```

### `guard`

Agent-side confidentiality enforcement. A pre-tool-use hook installed in
Claude Code (`cgh setup claude` / `cgh init`) consults the finding store
before every Read, Grep, Glob or Bash call and denies access to files
flagged confidential or carrying block-severity findings.

```
cgh guard [status|sync] [--root DIR]
```

- `status`: active mode, flagged file count, and an honest per-agent map
  (enforce / advisory / unprotected). An unprotected agent's only barrier
  is cgh's MCP-side gate.
- `sync`: mirror flagged paths into static `Read()` deny rules in
  `.claude/settings.local.json` (secure mode only; user-authored rules
  are never touched). Runs automatically after `cgh classify train`.

Fail posture follows the mode: `assist` fails open with a logged
warning, `secure` fails closed, a broken guard reads as blocked. Every
denial is logged to `.codegraph/activity.log`.

### `memory`

Shared memory hygiene. cgh's knowledge store is the canonical
cross-agent memory (standing instructions, session digests, learnings);
this command keeps it dense instead of noisy.

```
cgh memory review [--days N] [--root DIR]
```

Lists entries older than the window (default 90 days) so a human, or an
agent asked to tidy, can prune with the `knowledge_forget` MCP tool or
supersede them with fresh entries.

---

### `examples`

List runnable examples bundled inside the installed packages, or install
one locally to modify. Examples ship as package data, so this works with
no git checkout and no network. Each plugin can bundle its own.

```
cgh examples [list]
cgh examples install <name> [--dest DIR] [--package PKG] [--force]
```

**Example:**

```bash
cgh examples                                # name + description + source
cgh examples install pdf-to-vision          # copy it into ./pdf-to-vision
cgh examples install starter-config --dest .
```
