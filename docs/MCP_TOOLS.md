# MCP Tools

When running as an MCP server (`cgh serve`), codegraph exposes 52 tools, plus whatever installed plugins register.

### Architecture Awareness (call these FIRST)

| Tool | Description |
|------|-------------|
| `architecture_overview(max_files_per_role?)` | Compact map of all files grouped by layer (presentation/application/domain/infra/test/doc) and role (handler/router/component/store/…) with 1-line summaries: no Read needed |
| `domain_map(keyword, limit_per_role?)` | Every file whose path / role / module_doc mentions the keyword, grouped by role |
| `endpoints(path_pattern?, method?)` | List HTTP endpoints (FastAPI, Flask, Nuxt, Express, Django urls, NestJS, Spring, Gin/Echo) with their handlers: works cross-repo when `extra_dirs` is configured |

### Code Navigation

| Tool | Description |
|------|-------------|
| `symbol_lookup(name, role?, layer?)` | Find where a function, class, TF resource, or doc section is defined; optional `role` / `layer` filters |
| `find_callers(fn_name)` | Find all functions that call `fn_name` |
| `find_callees(fn_name, max_depth?)` | Functions `fn_name` calls; `max_depth>1` walks the CALLS chain forward and returns the ordered trace in one call |
| `imports_of(file_path)` | List modules imported by a file |
| `search_symbols(query, limit?, role?, layer?)` | Fuzzy search across all symbol types; optional `role` / `layer` filters |
| `subgraph(file_path, depth?)` | Find files related within N import hops (blast radius) |
| `graph_stats()` | Node and edge counts per type |

### Code Intelligence

| Tool | Description |
|------|-------------|
| `file_summary(file_path)` | One-shot orientation for a file: role/layer/lang, its functions and classes with line ranges, what it imports, and who imports it |
| `impact_of(symbol_or_file, max_depth?)` | Reverse blast radius: everything that transitively calls or imports the target, grouped by role/layer, with reaching endpoints |
| `path_between(src, dst, edge?)` | Shortest path between two symbols/files over `CALLS` or `IMPORTS` |
| `import_cycles(limit?)` | Detect import cycles (strongly-connected components) in the file import graph |
| `tests_for(symbol_or_file)` | Test files that exercise the target (inferred from imports/calls + role, not coverage) |
| `untested(role?, layer?)` | Source files that no test file imports |
| `hotspots(limit?)` | Change-risk ranking: git churn x import centrality x recency |
| `who_knows(file_path)` | Top authors of a file by commit count and recency (from git history) |

### Documentation

| Tool | Description |
|------|-------------|
| `search_docs(query, limit?)` | Search Markdown by heading title or body content |
| `doc_outline(file_path)` | Table of contents of a Markdown file |
| `doc_refs(symbol_name)` | Find all docs that reference a code symbol |

### Full-Text & AI Context

| Tool | Description |
|------|-------------|
| `fts_search(query, limit?, kind?)` | BM25-ranked full-text search over names + docstrings |
| `context_for_task(task, max_nodes?)` | Build ranked context from graph + FTS for any task |
| `find_dead_code(file_path?, include_private?)` | Find symbols with no incoming edges (potentially unused) |
| `fetch_and_index(url, ttl_hours?, force?)` | Fetch a URL, reduce to text, chunk and index it (gated network egress: http/https only, SSRF-guarded, refused in secure mode unless `allow_fetch`) |
| `search_fetched(query, limit?)` | Search the text of previously fetched pages, no further network |
| `purge_fetched(url?)` | Drop one URL's chunks, or all fetched content |

### Indexing

| Tool | Description |
|------|-------------|
| `scan_repo(verbose?)` | Full re-index of the entire repo |
| `index_changed_files(since?)` | Re-index only files changed since a git ref |
| `force_index(paths, confirmed?)` | Index files bypassing .gitignore (requires confirmation) |

### Visualization

| Tool | Description |
|------|-------------|
| `visualize_graph(scope, file_path?, symbol_name?, max_nodes?, format?)` | Generate Mermaid or Graphviz diagrams |

### Statistics

| Tool | Description |
|------|-------------|
| `call_stats()` | MCP tool usage statistics (calls, latency, errors) |
| `live_graph_stats()` | Polling-friendly snapshot: node counts + FTS size + scan freshness + timestamp |

### Scan Freshness & Incremental Updates

| Tool | Description |
|------|-------------|
| `scan_status()` | Is the graph in sync with `git HEAD`? Returns `fresh`, `indexed_sha`, `behind_by`, `changed_files` |
| `incremental_reindex()` | Surgical reindex: compares per-file git blob SHAs and touches only what actually changed since the last scan |
| `add_directory(path)` | Hot-add an external directory (sibling repo) to the graph: persists to config, scans, extends the watcher. No restart needed. |

---

---

[Back to the README](../README.md)
