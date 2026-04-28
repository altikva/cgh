# Configuration

codegraph uses a layered configuration system. Settings are resolved in order, with later sources overriding earlier ones.

---

## Resolution Order

1. **Hardcoded defaults** (built into codegraph)
2. **Global config**: `~/.codegraph/config.toml`
3. **Project config**: `.codegraph/config.toml`
4. **Environment variables**
5. **CLI flags**

---

## config.toml

TOML format. Created automatically by `cgh init`. Both the global and project configs use the same schema.

### Full Reference

```toml
# .codegraph/config.toml

[codegraph]
# Directories to skip during indexing (in addition to .gitignore).
# These are matched by exact directory name.
ignore_dirs = [
    ".git", ".codegraph", "node_modules", "__pycache__",
    ".venv", "venv", ".terraform", "dist", "build", ".next",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".coverage", "htmlcov", ".eggs", "*.egg-info",
]

# File glob patterns to skip.
ignore_patterns = ["*.min.js", "*.bundle.js", "*.map", "*.pyc", "*.pyo", "*.so", "*.dylib",
                   "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]

# Skip files larger than this (in KB). Prevents indexing generated files.
max_file_size_kb = 500

# Additional directories to include in the graph (relative to project root).
# Useful for multi-repo setups. Add with: cgh add-dir add ../frontend
# extra_dirs = ["../ondonne-frontend", "../ondonne-infra"]

# Owner log rotation (.codegraph/owner.log). Checked at owner spawn time —
# owners restart often (stop/start, --reindex, new sessions) so spawn-time
# rotation bounds disk use without an interceptor process.
# log_max_mb = 0       disables rotation entirely
# log_backup_count = 0 truncates instead of keeping backups
log_max_mb = 5
log_backup_count = 3


[parsers]
# Restrict which parsers are active. Omit to enable all available parsers.
# enabled = ["python", "typescript", "markdown"]

# Disable specific parsers (applied after enabled list).
# disabled = ["terraform"]


[mcp]
# Auto-start file watcher when the MCP server starts.
auto_watch = true

# Rebuild the index before accepting MCP connections.
reindex_on_start = true


[ruflo]
# Ruflo integration for context_for_task enrichment.
# Auto-detected at runtime if not set.
# enabled = true
```

### Section Details

#### `[codegraph]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ignore_dirs` | `list[str]` | See defaults below | Directory names to skip |
| `ignore_patterns` | `list[str]` | See defaults below | Glob patterns for files to skip |
| `max_file_size_kb` | `int` | `500` | Max file size in KB |
| `extra_dirs` | `list[str]` | `[]` | Extra directories to index (relative paths) |
| `log_max_mb` | `int` | `5` | Rotate `owner.log` when it exceeds this size at owner spawn. `0` disables rotation. |
| `log_backup_count` | `int` | `3` | How many `owner.log.N` backups to keep. `0` truncates without keeping backups. |

**Default `ignore_dirs`:**

```
.git, .codegraph, node_modules, __pycache__, .venv, venv,
.terraform, dist, build, .next, .tox, .mypy_cache,
.pytest_cache, .ruff_cache, coverage, .coverage, htmlcov,
.eggs, *.egg-info
```

**Default `ignore_patterns`:**

```
*.min.js, *.bundle.js, *.map, *.pyc, *.pyo, *.so, *.dylib,
package-lock.json, yarn.lock, pnpm-lock.yaml
```

#### `[parsers]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `list[str]` or omitted | all available | Whitelist of parser language names |
| `disabled` | `list[str]` | `[]` | Blacklist of parser language names |

Parser language names correspond to the `lang` attribute on each parser class: `python`, `typescript`, `terraform`, `markdown`, `vue`.

If `enabled` is set, only those parsers are active. `disabled` is then applied on top to further exclude.

#### `[mcp]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `auto_watch` | `bool` | `true` | Start file watcher alongside MCP server |
| `reindex_on_start` | `bool` | `true` | Rebuild index when `cgh serve` starts |

#### `[ruflo]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` or omitted | auto-detect | Enable/disable Ruflo memory integration |

When omitted, codegraph checks at runtime whether Ruflo MCP tools are available. If found, `context_for_task` includes Ruflo memory results.

---

## Global Config

Located at `~/.codegraph/config.toml`. Applied to all projects before the project-level config.

Useful for setting personal preferences that apply everywhere:

```toml
# ~/.codegraph/config.toml

[codegraph]
max_file_size_kb = 1000

[parsers]
disabled = ["terraform"]
```

---

## .cghignore

Optional file at the project root (next to `.gitignore`). Uses the same syntax as `.gitignore`. Patterns listed here are excluded from indexing in addition to `.gitignore` rules.

```gitignore
# .cghignore

# Skip generated API client
api/generated/
openapi_client/

# Skip vendored dependencies
vendor/

# Skip large data files
fixtures/*.json
```

codegraph already respects `.gitignore` via `git ls-files`. The `.cghignore` is for files that are tracked by git but should not be indexed (e.g., generated code, vendored libs).

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CODEGRAPH_ROOT` | Override the project root directory | `/home/user/myproject` |
| `CODEGRAPH_DIR` | Override the `.codegraph/` directory location | `/tmp/codegraph-index` |
| `CODEGRAPH_AUTH_KEY` | MCP server auth key (auto-generated by `cgh init`) | `<token_urlsafe(32)>` |
| `CODEGRAPH_RUFLO_ENABLED` | Force Ruflo integration on or off | `1`, `true`, `yes`, `0`, `false`, `no` |

### `CODEGRAPH_AUTH_KEY`

The MCP server auth key. Auto-generated by `cgh init` and stored in `.codegraph/auth.key`. Injected into `.mcp.json` env block by `cgh init` and `cgh setup`. Defense-in-depth for future HTTP transport.

```bash
# Key is managed automatically — no manual steps needed
# To regenerate: delete .codegraph/auth.key and run cgh init
```

### `CODEGRAPH_ROOT`

Overrides the project root. Useful when running codegraph from a different directory than the project:

```bash
CODEGRAPH_ROOT=/home/user/my-project cgh stats
```

### `CODEGRAPH_DIR`

Overrides where the `.codegraph/` directory is located. By default it is `<project_root>/.codegraph/`. This lets you store the index in a different location (e.g., a tmpfs for speed, or a shared location):

```bash
CODEGRAPH_DIR=/tmp/my-project-codegraph cgh index
```

### `CODEGRAPH_RUFLO_ENABLED`

Force-enable or force-disable the Ruflo integration, bypassing auto-detection:

```bash
CODEGRAPH_RUFLO_ENABLED=false cgh serve --watch
```

---

## File Discovery

codegraph discovers files to index using this strategy:

1. **Git repos**: `git ls-files --exclude-standard` -- respects `.gitignore`, `.git/info/exclude`, and global gitignore
2. **Non-git directories**: `os.walk` with `ignore_dirs` and `ignore_patterns` filtering
3. **Extra directories**: paths listed in `extra_dirs` are scanned using the same strategy
4. **Force-index**: `cgh force-index` bypasses all ignore rules for specified paths

Files are then filtered by:
- Extension must match a registered parser
- File size must be under `max_file_size_kb`
- Parser must not be disabled

---

## Storage Layout

After initialization and indexing, the `.codegraph/` directory contains:

```
.codegraph/
    config.toml     # project configuration
    graph.db/       # Kuzu graph database (nodes + edges)
    fts.db          # SQLite FTS5 full-text search index
    call_log.db     # SQLite log of MCP tool calls
```

Typical storage usage for a 200-file project: 10-20 MB total.

Use `cgh compact` to vacuum the SQLite databases and reclaim space.

---

## Precedence Examples

**Disable Terraform globally, re-enable it for one project:**

```toml
# ~/.codegraph/config.toml
[parsers]
disabled = ["terraform"]
```

```toml
# my-infra-project/.codegraph/config.toml
[parsers]
disabled = []
```

**Override max file size via env var:**

```bash
# config.toml says 500, but override to 2000 for this run
CODEGRAPH_ROOT=. cgh index  # uses config.toml value
# env vars don't override max_file_size_kb directly --
# edit .codegraph/config.toml instead
```

**Multi-repo indexing:**

```toml
# ondonne-api/.codegraph/config.toml
[codegraph]
extra_dirs = ["../ondonne-frontend", "../ondonne-infra"]
```

Then `cgh index` indexes all three repos into a single graph.
