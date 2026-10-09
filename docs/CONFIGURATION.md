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

# Let fetch_and_index / `cgh fetch` reach the network (off by default).
# allow_fetch = false

# Additional directories to include in the graph (relative to project root).
# Useful for multi-repo setups. Add with: cgh add-dir add ../frontend
# extra_dirs = ["../ondonne-frontend", "../ondonne-infra"]

# Owner log rotation (.codegraph/owner.log). Checked at owner spawn time:
# owners restart often (stop/start, --reindex, new sessions) so spawn-time
# rotation bounds disk use without an interceptor process.
# log_max_mb = 0       disables rotation entirely
# log_backup_count = 0 truncates instead of keeping backups
log_max_mb = 5
log_backup_count = 3

# Federated subrepos: sub-projects with their own .codegraph/ index. The
# parent indexes only files OUTSIDE these paths and federates queries
# (read-only) to the children at runtime. Each subrepo can be a separate
# git repo with its own .gitignore; the parent doesn't try to walk into
# them. Add via: cgh federate add ../child-repo
# subrepos = ["./apps/api", "./apps/web", "../shared-lib"]

# When this repo's owner starts, also start the owner of every initialized
# subrepo whose owner is idle (watcher included, so child indexes stay
# fresh). Children started this way stop on their own shortly after the
# parent owner exits. Set to false to opt out.
# federate_auto_up = true


[parsers]
# Restrict which parsers are active. Omit to enable all available parsers.
# enabled = ["python", "typescript", "markdown"]

# Disable specific parsers (applied after enabled list).
# disabled = ["terraform"]


[terraform]
# Map a remote module source to a local checkout (opt-in, read-only).
# module_sources = { "git::https://github.com/altikva/gcp-modules" = "../gcp-modules" }


[mcp]
# Auto-start file watcher when the MCP server starts.
auto_watch = true

# Rebuild the index before accepting MCP connections.
reindex_on_start = true

[bob]
# IBM Bob session hooks: ask for a checkpoint after this many tool calls
# without one, and (opt-in, 0 = off) block a tool call past this many.
checkpoint_every = 40
checkpoint_gate = 0


[plugins]
# Narrow or bar installed plugins without uninstalling them.
# enabled = ["docs", "codegen"]
# disabled = ["bugreport"]

# Per-plugin settings live in [plugin.<name>] tables (singular). See each
# plugin's README for its keys; the template written by `cgh init` lists
# the first-party ones commented out.
# [plugin.pii]
# scan_on_index = false
```

### Section Details

#### `[plugin.<name>]` index-time scanning

| Key | Plugin | Default | Description |
|-----|--------|---------|-------------|
| `scan_on_index` | pii, vision | `false` | Register the plugin's scanner so it runs on every indexed file (vision also indexes images). Off, the plugin only answers its own commands (`cgh pii scan`, `cgh vision`). |
| `pii` | pii | `false` | Add the PII patterns (emails, phones, IBANs, cards) to the secret ones, for `cgh pii scan` and the index-time scanner. `codegraph.sdk.scan_text` keeps them on unless this is set to `false`. |

#### Ignored keys

These keys still load without error but nothing reads them. A config
that carries one gets a single notice per process on stderr (never in
hook output) and in `cgh status` / `cgh doctor`. Delete the lines.

| Key | Since | Why |
|-----|-------|-----|
| `[codegraph] mode = "secure"` | 0.15.0 | Secure mode was removed. `mode = "assist"` stays silent. |
| `[plugin.summarize] allow_pii`, `egress`, `claude_model`, `gemini_model` | 0.15.0 | cgh-summarize 0.3.0 dropped its cloud backends and egress gate, and cgh refuses older releases. |

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
| `allow_fetch` | `bool` | `false` | Let the `fetch_and_index` MCP tool and `cgh fetch` reach the network. Private and loopback hosts stay refused, every fetch is logged. |
| `mode` | `str` | `"assist"` | Deprecated. `"secure"` was removed in 0.15.0: it is ignored, with a notice on stderr and in `cgh status` / `cgh doctor`. Delete the line. |
| `federate_auto_up` | `bool` | `true` | When the parent owner starts, also start each initialized subrepo's owner (with watcher) if it is idle. Those children live exactly as long as the parent owner. |

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

#### `[terraform]`

`module_sources` maps a remote Terraform module source (git URL, registry
address) to a local directory, so a `module` block with that source links
to the module's variables and outputs like a local `./` source does: its
arguments to the module's `var.<name>`, `module.m.out` to the module's
output. Without it a remote module is opaque.

```toml
[terraform]
module_sources = { "git::https://github.com/altikva/gcp-modules" = "../gcp-modules" }
```

- The key is matched as a prefix of the source's package, the part before
  its `//subdir`, on a path boundary: `git::` and a trailing `.git` are
  ignored on both sides, and the longest matching key wins. So the key
  above maps
  `git::https://github.com/altikva/gcp-modules.git//modules/kms?ref=v0.1.0`
  to `../gcp-modules/modules/kms`. A registry address works the same way
  (`"acme/kms/google" = "../modules/kms"`).
- The `//subdir` is joined to the mapped directory. The `?ref=` is
  ignored: links follow whatever the local checkout holds, which may differ
  from the pinned ref.
- Paths are relative to the project root, or absolute.
- When the mapped directory is indexed by this graph (inside the repo or an
  `extra_dirs` entry) the links are ordinary edges. Otherwise (outside every
  index, or inside a federated subrepo) cgh reads that directory's
  variables and outputs into this graph when a calling file is indexed:
  read-only, nothing written there, nothing fetched over the network.
- A change to the mapping takes effect on the next full re-index
  (`cgh index --force`).

#### `[mcp]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `auto_watch` | `bool` | `true` | Start file watcher alongside MCP server |
| `reindex_on_start` | `bool` | `true` | Rebuild index when `cgh serve` starts |

#### `[bob]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `checkpoint_every` | `int` | `40` | Tool calls without a cgh checkpoint before Bob's next prompt carries a reminder |
| `checkpoint_gate` | `int` | `0` | When above 0, block one tool call past this many calls without a checkpoint, until the model saves. Re-run `cgh setup bob` after changing it |

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

# Starting the parent owner also starts each child's owner (with its
# watcher) if it is idle, so child indexes stay fresh. Those children
# stop on their own once the parent owner exits. Opt out with
# federate_auto_up = false in the parent's config.toml.
#
# To start children that OUTLIVE the parent (keepalive marker):
cgh federate up                    # spawns `cgh _serve_owner --watch` per child
cgh federate down                  # stops them all
```

**Owner lifecycle**: the parent reads each child's `.codegraph/` files
directly (read-only), children's owners exist only to keep their own index
fresh. When the parent owner starts it also starts the owner (with watcher)
of every initialized child whose owner is idle, unless `federate_auto_up =
false`. Children started this way carry the parent owner's pid as a worker
marker, so they stop on their own a few seconds after the parent owner
exits. Children that were already up are left alone: they keep whatever
lifecycle started them, and two repos federating each other can't keep each
other alive forever. `cgh federate up` remains the explicit way to start
children that outlive the parent (keepalive marker), `cgh federate down`
stops them. If a child's owner is mid-write when the parent queries it,
that scope returns `partial: true / warnings: [...]`; results from other
scopes still flow.

**What's federated** (read-only, scope-tagged):

| Tool | Aggregation |
|---|---|
| `symbol_lookup`, `search_symbols`, `find_callers`, `find_callees` | Concat per scope |
| `imports_of`, `subgraph` | Concat: cross-repo edges are NOT inferred (each scope's IMPORTS graph is canonical for its own files) |
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
# Key is managed automatically: no manual steps needed
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
    graph.duckdb     # DuckDB graph database (nodes + edges): default backend
    graph.sqlite     # SQLite graph database (nodes + edges): standalone binary / CGH_DB=sqlite
    fts.db           # SQLite FTS5 full-text search index
    call_log.db      # SQLite log of MCP tool calls
```

Typical storage usage for a 200-file project: 4-8 MB total on DuckDB.

Use `cgh compact` to vacuum the SQLite databases and reclaim space.

---

## Backend selection

The default graph backend is **DuckDB**. The standalone binary ships a SQLite backend instead (no bundled DuckDB library). Resolution order when opening a repo's graph:

1. `CGH_DB` env var, if set to `duckdb` or `sqlite`.
2. Auto-detect from `.codegraph/`: `graph.duckdb` → DuckDB, `graph.sqlite` → SQLite.
3. Fresh repos with no `.codegraph/` → DuckDB when the DuckDB library is importable, otherwise SQLite.

Pin a specific backend per shell:

```bash
CGH_DB=duckdb cgh index    # force DuckDB
CGH_DB=sqlite cgh index    # force the SQLite backend
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
