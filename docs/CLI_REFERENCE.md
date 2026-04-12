# codegraph CLI Reference

Complete reference for all 20 CLI commands. Both `codegraph` and `cgh` (short alias) work identically.

Global flag available on all commands:

```
--root <DIR>    Target a different project root (default: current directory)
```

---

## Getting Started

### `init`

Interactive wizard that initializes codegraph in a project. Detects AI tools, installs MCP server configs and hooks, scans for parseable files, and optionally runs the first index.

```
cgh init [--yes | -y] [--root DIR]
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Accept all defaults, skip interactive prompts |

**What it does:**

1. Creates `.codegraph/` directory and `config.toml`
2. Generates MCP auth key (`.codegraph/auth.key`)
3. Adds `.codegraph/` and `.codegraph/auth.key` to `.gitignore`
4. Detects installed AI tools (Claude Code, Cursor, Codex, Gemini)
5. Prompts to install MCP configs for detected tools (with auth key in env)
6. Counts parseable files by language
7. Optionally runs `cgh index`

**Example:**

```bash
cgh init
cgh init --yes    # CI-friendly, no prompts
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
| `target` | `claude`, `cursor`, `codex`, `gemini`, `all` |

**Example:**

```bash
cgh setup claude     # writes .mcp.json
cgh setup cursor     # writes .cursor/mcp.json
cgh setup all        # writes configs for all tools
```

---

### `index`

Build or rebuild the full code graph. Discovers files via `git ls-files` (falls back to `os.walk` in non-git dirs). Parses every supported file and stores nodes/edges in the Kuzu graph DB and BM25 FTS index.

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

## Maintenance

### `doctor`

Run a health check that verifies all codegraph components are operational.

```
cgh doctor [--root DIR]
```

Checks performed:

1. `.codegraph/` directory exists
2. `graph.db` (Kuzu) is accessible
3. `fts.db` (SQLite FTS5) is accessible
4. `call_log.db` is accessible
5. `config.toml` is valid TOML
6. Parsers load successfully
7. `git` is available in PATH
8. `.cghignore` exists (optional)
9. `fastmcp` is importable (MCP server dependency)

---

### `compact`

Vacuum SQLite databases (`fts.db`, `call_log.db`) to reclaim space. Shows before/after sizes. Kuzu's `graph.db` is displayed for reference but is not vacuumable via this command.

```
cgh compact [--root DIR]
```

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
