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

# Federated subrepos — sub-projects with their own .codegraph/ index. The
# parent indexes only files OUTSIDE these paths and federates queries
# (read-only) to the children at runtime. Each subrepo can be a separate
# git repo with its own .gitignore; the parent doesn't try to walk into
# them. Add via: cgh federate add ../child-repo
# subrepos = ["./apps/api", "./apps/web", "../shared-lib"]


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
| `subrepos` | `list[str]` | `[]` | Federated sub-projects with their own `.codegraph/` index. Parent indexes only files outside these paths and federates read-only queries to them at runtime. Manage with `cgh federate add/remove/list/verify`. |

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

## Federated subrepos

When you have a parent project that contains (or sits next to) several
sub-projects each with their own `git` repository and their own
`.codegraph/` index, the parent should NOT try to re-index everything.
Each sub-repo's `.gitignore` and parsers are self-contained, and re-indexing
from the parent would either miss files (its `git ls-files` doesn't see the
children's tracked files) or duplicate work.

The federation model: the parent acts as a **passe-plat**. It indexes only
files that don't fall under any declared subrepo, and at runtime each MCP
read tool fans out to the children's `.codegraph/` databases (read-only)
and aggregates results, tagging each with a `scope` field (`parent`,
`<child-name>`).

**Setup**

```bash
# In each subrepo (one-time, by whoever owns that repo)
cd apps/api && cgh init && cgh index

# In the parent
cd ../..
cgh init                           # creates parent's own .codegraph/, auto-detects nested subrepos
cgh federate add ./apps/api ./apps/web ../shared-lib    # if you want to add manually
cgh federate list                  # status table per child (status, owner, git, path)
cgh index                          # parent indexes only its own files
cgh serve --background --watch     # parent owner federates queries to children

# Optional: keep each child's own owner alive too (so its index stays
# fresh as files in the child's tree are edited). Without this, the parent
# can still read each child's DB read-only, but the child's data may go
# stale if no-one runs the child's watcher.
cgh federate up                    # spawns `cgh _serve_owner --watch` per child
cgh federate down                  # stops them all
```

**Owner lifecycle**: the parent reads each child's `.codegraph/` files
directly (read-only). It does NOT auto-spawn child owners — children's
owners exist only to keep their own index fresh. `cgh federate up` is the
explicit way to ensure every child has its own watcher running. If a
child's owner is mid-write when the parent queries it, that scope returns
`partial: true / warnings: [...]`; results from other scopes still flow.

**What's federated** (read-only, scope-tagged):

| Tool | Aggregation |
|---|---|
| `symbol_lookup`, `search_symbols`, `find_callers`, `find_callees` | Concat per scope |
| `imports_of`, `subgraph` | Concat — cross-repo edges are NOT inferred (each scope's IMPORTS graph is canonical for its own files) |
| `pattern_search` | Runs ripgrep in each scope's tree |
| `fts_search` | Concat + sort by score (BM25 not renormalized across repos) |
| `search_docs`, `doc_outline`, `doc_refs` | Concat |
| `architecture_overview` | Returns `{by_scope: {parent: {…}, child1: {…}}}` when subrepos present |
| `domain_map`, `endpoints` | Concat with per-result scope tag |
| `find_dead_code` | Per-scope analysis with explicit caveat: a symbol "dead" in scope X may be live via cross-repo callers |

**What's NOT federated** (parent-local only):
- `knowledge_*`, `memory_*`, `plan_*` (each project keeps its own)
- `index`, `force_index`, `incremental_reindex` (write-side, parent only)
- `context_for_task`, `session_*`, `call_stats` (parent-local)

**Edge cases**

- Subrepo not initialized → `cgh federate add` warns, skipped at query time
- Subrepo's graph DB locked by its own owner → that scope returns
  `error: db unavailable`; results include `partial: true` + `warnings: [...]`
- Subrepo deleted from disk → marked `unreachable` in `cgh federate list`,
  silently skipped at query time
- Federation membership changes mid-session → restart the parent's owner
  (`cgh serve --stop && cgh serve --background`) to refresh the watcher's
  cached subrepo list

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
    config.toml      # project configuration
    graph.duckdb     # DuckDB graph database (nodes + edges) — default backend
    graph.db/        # Kuzu graph database (nodes + edges) — only when CGH_DB=kuzu
    fts.db           # SQLite FTS5 full-text search index
    call_log.db      # SQLite log of MCP tool calls
```

Typical storage usage for a 200-file project: 4-8 MB total on DuckDB (~3x smaller than the legacy Kuzu layout).

Use `cgh compact` to vacuum the SQLite databases and reclaim space.

---

## Backend selection

Since v0.5 the default graph backend is **DuckDB**. Resolution order when opening a repo's graph:

1. `CGH_DB` env var, if set to `duckdb` or `kuzu`.
2. Auto-detect from `.codegraph/`: `graph.duckdb` → DuckDB, `graph.db` → Kuzu.
3. Fresh repos with no `.codegraph/` → DuckDB.

`cgh init` auto-migrates repos that only have `graph.db` by re-indexing into `graph.duckdb` and verifying counts. See [`cgh migrate-to-duckdb`](CLI_REFERENCE.md#migrate-to-duckdb) for the manual command and its `stale_kuzu` classifier rules.

Pin a specific backend per shell:

```bash
CGH_DB=duckdb cgh index    # force DuckDB
CGH_DB=kuzu   cgh index    # opt back into Kuzu (kept for parity / debugging)
```

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
