# MCP Tools

When running as an MCP server (`cgh serve`), cgh exposes 62 tools, plus whatever installed plugins register.

In a federated project (`subrepos` in `config.toml`), the tools that fan out tag each row with its `scope` and add a `scopes` map to the response: for each scope, the `head` commit and `branch` checked out, the `indexed_commit` graph answers come from, and `index_at_head`. An answer can then cite the exact commit it was read at.

Every answer an agent gets stays under a size ceiling: about 10 KB, 6 KB for `context_for_task`, 2.5 KB for `pattern_search`, 2 KB for `file_summary`. A longer answer drops whole list items from its tail, so the JSON stays valid, and carries `truncated: true`, `dropped` and a `hint` on how to narrow the call. A tool given `max_tokens` or `budget_kb` keeps that budget instead, and calls from the `cgh` CLI and the hooks get the whole answer.

### Architecture Awareness (call these FIRST)

| Tool | Description |
|------|-------------|
| `architecture_overview(max_files_per_role?)` | Compact map of all files grouped by layer (presentation/application/domain/infra/test/doc) and role (handler/router/component/store/…) with 1-line summaries, plus a short `subsystems` list (the biggest communities): no Read needed |
| `domain_map(keyword, limit_per_role?)` | Every file whose path / role / module_doc mentions the keyword, grouped by role |
| `communities(resolution?, min_size?, max_communities?)` | Subsystems detected from the import and call graph (modularity clustering, deterministic), each named from its own files, with its size, central file, files, top symbols, cohesion and strongest links to other communities. Files linked to nearly everything are listed apart as shared utilities; test files are left out. Federated per scope, cached until the index changes |
| `architecture_report(max_items?, resolution?)` | One Markdown page built from the graph, no LLM: subsystems with names and sizes, shared utilities, pivot files (change-risk hotspots when the repo has commit history, each marked when no test imports it), bridges (the only link between two subsystems, files reaching many of them), unexpected links (layer inversions, calls with no import behind them), import cycles, and rule-made questions worth asking. Capped sections with file:line references, one section per federated scope |
| `endpoints(path_pattern?, method?, include_tests?)` | List HTTP endpoints (FastAPI, Flask, Nuxt, Express, Django urls, NestJS, Spring, Gin/Echo) with their handlers and, for FastAPI / Flask, full paths composed from router and include prefixes. A full path, a glob or a path with other parameter names finds a route; an unmatched path falls back to a flagged suffix match. Routes in test files are left out unless `include_tests`: works cross-repo when `extra_dirs` is configured |
| `packages(name?, include_external?, limit?)` | Packages declared by `package.json` (npm / pnpm / yarn workspaces) and `pyproject.toml` (uv workspaces, Poetry), each with its internal dependencies (packages declared in the same repo), external ones (name and spec, as a count unless `include_external` or `name`), the packages depending on it, and its workspace; internal dependency cycles; and the MCP servers declared in `.mcp.json`, `.cursor/mcp.json` and `.vscode/mcp.json` (transport, command or url, env and header key names, never their values). Federated per scope |

### Code Navigation

| Tool | Description |
|------|-------------|
| `symbol_lookup(name, role?, layer?)` | Find where a function, class, TF resource, or doc section is defined (each definition carries its `name`, the title for a doc section); optional `role` / `layer` filters. A function or class with rationale comments lists the first three in `rationale` |
| `find_callers(fn_name)` | Find all functions that call `fn_name`; each row says how its edge was obtained in `resolution` (see below) |
| `find_callees(fn_name, max_depth?)` | Functions `fn_name` calls, each with its `resolution`; `max_depth>1` walks the CALLS chain forward and returns the ordered trace in one call. A call to a Protocol or ABC method with a single implementation in the scope continues into it (a row marked `interface`, with `via`); with several, the row carries `implementations` and the walk stops. `Class.method` / `module.function` starts from that definition only |
| `imports_of(file_path)` | List modules imported by a file; each row's `kind` is empty for a plain import, `reexport` for a JS / TS `export ... from` and `dynamic` for `import('./x')` |
| `search_symbols(query, limit?, role?, layer?, kinds?, name_only?)` | Fuzzy search across all symbol types; optional `role` / `layer` filters, `kinds` (comma list, e.g. `function,class`) to limit the kinds, `name_only` to skip TF type and section body matches |
| `indexed_files(pattern?, limit?, path?)` | Indexed file paths containing `pattern` (`total` plus the first `limit`), or with `path` whether that one file is indexed. Sees files that define no symbol, unlike the FTS index |
| `pattern_search(pattern, glob?, max_results?, regex?, case_sensitive?, repo?, ref?)` | Regex or literal search returning hits grouped by file (`files: [{file, hits: [[line, text]]}]`, about 2.5 KB at most), instead of Grep. `ref` searches the tree at a branch, tag or commit straight from git; `repo` targets a sibling repo declared under `[codegraph] siblings` in `~/.codegraph/config.toml` |
| `file_at_ref(path, ref, repo?, start_line?, end_line?)` | A file's lines as they are at a branch, tag or commit, from git, without a checkout (this repo or a declared sibling; at most 400 lines per call) |
| `subgraph(file_path, depth?, max_tokens?)` | Find files related within N import hops (blast radius); `max_tokens` (about 4 characters per token of JSON) trims the farthest files first and reports `truncated` and `dropped` |
| `graph_stats()` | Node and edge counts per type |

**Call edge resolution.** The rows of `find_callers` and `find_callees`, the
impacted functions of `impact_of` and the `hops` of `path_between` over CALLS
carry a `resolution`, read from the call sites the caller recorded:

| Value | Meaning |
|-------|---------|
| `resolved` | The call's shape fixes the target: a call to a function of the same file, an import naming the target's file, `self.f()` on the caller's class or a base, a receiver whose class is known, or a hit of the opt-in precise resolver |
| `name` | Matched by the callee's name only (receiver of unknown type, no import binding it, a language without call shapes), one definition of that name |
| `ambiguous` | Matched by name, and the caller links to several definitions of it (the rows that list `targets`) |
| `interface` | Reached only through an interface method the target implements (the rows with `via`) |

Treat `name` and `ambiguous` as guesses. A caller with several edges to the
queried name keeps the most certain one.

### Code Intelligence

| Tool | Description |
|------|-------------|
| `file_summary(file_path)` | One-shot orientation for a file: role/layer/lang, its functions and classes with line ranges, what it imports, and who imports it |
| `impact_of(symbol_or_file, max_depth?, focus?, max_tokens?)` | Reverse blast radius: everything that transitively calls or imports the target, grouped by role/layer, with reaching endpoints; an impacted function carries the `resolution` of the edge that reached it. The single implementation of a Protocol or ABC method also counts the callers of that method. `max_tokens` trims the farthest nodes first (`truncated`, `dropped`). JS / TS dynamic imports count as imports, and a barrel re-exporting the target passes it on to its own importers in the same hop; each impacted file says `via` which link reached it (`import`, `reexport`, `dynamic`, `interface`, `terraform`) |
| `path_between(src, dst, edge?)` | Shortest path between two symbols/files over `CALLS` or `IMPORTS`; over CALLS, `hops` gives each step's `resolution`, and a Protocol or ABC method with a single implementation leads on to it |
| `import_cycles(limit?)` | Detect import cycles (strongly-connected components) in the file import graph |
| `shared_types(name?, min_confidence?, limit?)` | Types mirrored across federated repos, deduced at query time: a class, interface or object type alias defined under the same name in two scopes whose field names agree (a backend Pydantic model and the frontend TypeScript interface, snake_case and camelCase equal). Each pair is `{name, scopes, matched_fields, confidence, deduced: true, definitions, only_in}`; nothing is stored and children are only read. Python and TypeScript / JavaScript |
| `tests_for(symbol_or_file)` | Test files that exercise the target (inferred from imports/calls + role, not coverage) |
| `impact_report(changed_files)` | The `cgh impact --json` payload for a change set: changed symbols, importers by role/layer, endpoints, tests to run (parent scope only) |
| `pr_overlap(limit?)` | Open pull requests that collide, read through the GitHub CLI: pairs that change the same symbol (`strong`), where one PR's changed code calls what the other changes (`indirect`, one hop), or that edit the same files (`weak`). Returns `{error}` when `gh` is missing, not authenticated or the repo has no GitHub remote (parent scope only) |
| `untested(role?, layer?)` | Source files that no test file imports |
| `hotspots(limit?)` | Change-risk ranking: git churn x import centrality x recency |
| `who_knows(file_path)` | Top authors of a file by commit count and recency (from git history) |
| `rationale(target?, tag?, limit?)` | Rationale comments of a symbol (bare name or `Class.method`; a class includes its members), a file, or the whole index: comments opening with `WHY:`, `NOTE:`, `HACK:`, `SAFETY:`, `INVARIANT:`, `IMPORTANT:`, `WARNING:` or `DECISION:`, or citing an ADR (`ADR-0007`, `docs/adr/0007-x.md`). Each row gives `file`, `line`, `tag`, `text` and the `symbol` it documents (the definition right below, else the one around it, else the module), with `adr_file` when the ADR resolves to an indexed Markdown file. Python, JS / TS, Vue, Go, Rust, Java, C#, Ruby, Terraform, SQL. Same query as `cgh why` |

### Documentation

| Tool | Description |
|------|-------------|
| `search_docs(query, limit?)` | Search Markdown by heading title or body content |
| `doc_outline(file_path)` | Table of contents of a Markdown file |
| `doc_refs(symbol_name)` | Find all docs that reference a code symbol; `match` is `exact` when an inline code span names the symbol and nothing else, `heuristic` for a name picked out by its shape or a text mention |

### Full-Text & AI Context

| Tool | Description |
|------|-------------|
| `fts_search(query, limit?, kind?)` | BM25-ranked full-text search over names + docstrings |
| `context_for_task(task, max_nodes?)` | Build ranked context from graph + FTS for any task; the rationale comments of the served functions and classes come along (a few, short) in `rationale` and under each symbol |
| `find_dead_code(file_path?, include_private?)` | Find symbols with no incoming edges (potentially unused) |
| `fetch_and_index(url, ttl_hours?, force?)` | Fetch a URL, reduce to text, chunk and index it (gated network egress: http/https only, SSRF-guarded, refused unless `[codegraph] allow_fetch = true`) |
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
| `live_graph_stats()` | Polling-friendly snapshot: node and edge counts + FTS size + scan freshness + timestamp |

### Scan Freshness & Incremental Updates

| Tool | Description |
|------|-------------|
| `scan_status()` | Is the graph in sync with `git HEAD`? Returns `fresh`, `indexed_sha`, `behind_by`, `changed_files` |
| `incremental_reindex()` | Surgical reindex: compares per-file git blob SHAs and touches only what actually changed since the last scan |
| `add_directory(path)` | Hot-add an external directory (sibling repo) to the graph: persists to config, scans, extends the watcher. No restart needed. |

### Knowledge

| Tool | Description |
|------|-------------|
| `knowledge_record(title, body, kind?, tags?, file_refs?, session_id?, supersedes?)` | Save a decision, gotcha, pattern, style rule, glossary term or note for later sessions |
| `knowledge_search(query, kind?, limit?, scope?)` | Search saved notes (not the code), ranked by relevance and by recorded outcomes |
| `knowledge_list(kind?, tag?, session_id?, limit?, offset?, entry_id?)` | Browse saved notes as briefs, 10 per page near 2.5 KB: useful entries first, corrected ones last, newest first inside each tier. `entry_id` reads one entry whole |
| `knowledge_outcome(verdict, entry_id?, query?, note?, file_refs?, corrected_by?, session_id?)` | Record whether relying on an entry, or on some files, worked: `useful`, `dead_end`, or `corrected` (the entry was wrong; `corrected_by` names the fix). Verdicts fade with a half-life (`[codegraph] knowledge_half_life_days`, 30 by default) and give each entry and file a status: `preferred`, `useful`, `contested`, `dead_end`, `corrected` or `faded`. Search and list show it as an `outcome` summary, and `resume` carries the strongest recent dead ends under `known_dead_ends` |
| `knowledge_terms(min_count?)` | Every tag with its count: the glossary of captured topics |
| `knowledge_forget(entry_id)` | Delete one entry and its outcomes |

### Usage log

Every tool call is logged to `.codegraph/call_log.db` with its `origin`: `agent` (an MCP client through `cgh serve`), `cli` (a `cgh` command asking the running owner), `hook` (the same from an agent or git hook) or `internal` (an in-process call). Rows logged before this column existed have no origin and show as `unknown`. `cgh stats`, `cgh logs` and `call_stats()` show the split, so hook traffic is not read as agent choices.

---

---

[Back to the README](../README.md)
