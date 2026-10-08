# MCP Tools

When running as an MCP server (`cgh serve`), codegraph exposes 54 tools, plus whatever installed plugins register. Each tool's description opens with the question it answers, says when to pick another tool, and is kept short because every agent session loads all of them.

Questions about the code go to the code tools. `knowledge_search` searches notes saved in earlier sessions, never the code.

### Find code

| Tool | Question it answers |
|------|---------------------|
| `symbol_lookup(name, role?, layer?)` | Where is X defined? (function, class, TF resource, doc section) |
| `search_symbols(query, limit?, role?, layer?, kinds?, name_only?)` | Which symbols have a name like X? (half-known name) |
| `fts_search(query, limit?, kind?)` | Where is the code that does this, described in words? A whole sentence works, French or English |
| `pattern_search(pattern, glob?, regex?, case_sensitive?, max_results?)` | Where does this exact string or regex occur? Use instead of Grep, `git grep`, `rg` |
| `context_for_task(task, max_nodes?, session_id?, include_shown?)` | What code, notes and plans matter for this task? Call first on a coding task |
| `find_callers(fn_name)` | Who calls X? |
| `find_callees(fn_name, max_depth?)` | What does X call? `max_depth > 1` follows the chain in one call |
| `file_summary(file_path)` | What is in this file, before I read it? |
| `imports_of(file_path)` | What does this file import? |
| `subgraph(file_path, depth?)` | Which files does this file import, and which import it? |
| `path_between(src, dst, edge?)` | How does A reach B? (over `CALLS` or `IMPORTS`) |

### Structure

| Tool | Question it answers |
|------|---------------------|
| `architecture_overview(max_files_per_role?)` | How is this codebase organised? Files by layer and role, no Read needed |
| `domain_map(keyword, limit_per_role?)` | Which files touch this feature? |
| `endpoints(path_pattern?, method?)` | Which HTTP routes exist, and which handler serves each? |

### Change impact and tests

| Tool | Question it answers |
|------|---------------------|
| `impact_of(symbol_or_file, max_depth?, focus?)` | What depends on this symbol or file? (transitive callers or importers) |
| `impact_report(changed_files)` | What does this change set break, and which tests should I run? Same payload as `cgh impact --json` |
| `tests_for(symbol_or_file)` | Which tests cover this symbol or file? |
| `untested(role?, layer?)` | Which source files have no test? |
| `find_dead_code(file_path?, include_private?)` | What code looks unused? Per-scope candidates, never a verdict |
| `import_cycles(limit?)` | Are there import cycles? |
| `hotspots(limit?)` | Where would a regression hurt most? (git churn x import centrality) |
| `who_knows(file_path)` | Who knows this file? (top authors from git history) |

### Documentation and diagrams

| Tool | Question it answers |
|------|---------------------|
| `search_docs(query, limit?)` | Which Markdown doc covers this topic? |
| `doc_outline(file_path)` | What sections does this Markdown file have? |
| `doc_refs(symbol_name)` | Which docs mention this symbol? |
| `visualize_graph(scope?, file_path?, symbol_name?, max_nodes?, format?)` | Can I see these relationships as a diagram? (Mermaid or DOT) |

### Notes, memory, plans and sessions (not the code)

| Tool | Question it answers |
|------|---------------------|
| `knowledge_search(query, kind?, limit?, scope?)` | Was this problem solved or decided before? |
| `knowledge_list(kind?, limit?, offset?, session_id?, tag?)` | What notes were saved in earlier sessions? |
| `knowledge_record(title, body, kind?, tags?, file_refs?, session_id?, supersedes?)` | How do I save what I just learned for future sessions? |
| `knowledge_terms(min_count?)` | Which topics do the saved notes cover? |
| `knowledge_forget(entry_id)` | How do I delete a saved note? |
| `memory_search(query, kind?, limit?)` | What did the user tell me before about this? |
| `memory_list(kind?)` | Which memory entries exist? |
| `plan_search(query, limit?)` | Is there a plan for this already? |
| `plan_list(agent_only?, limit?)` | Which plan files exist? |
| `resume(session_id?, task?, budget_kb?, scope?)` | Where did the last session leave off? Once at session start |
| `checkpoint(session_id, digest, title?)` | How do I save where this task stands before a clear or compaction? |
| `compact_session(session_id, digest, title?, tags?, file_refs?)` | How do I keep a summary of this whole session as a note? |
| `session_reset(session_id)` | How do I let `context_for_task` show already-seen results again? |

### Index maintenance

The watcher reindexes saved files and the git hooks (`cgh hooks install`) run `incremental_reindex` after pull, merge, checkout and rebase, so an agent rarely calls these. `scan_status` is the one to check when results look stale.

| Tool | Question it answers |
|------|---------------------|
| `scan_status()` | Is the index up to date with git? |
| `incremental_reindex()` | How do I refresh the index after a pull, checkout or rebase? |
| `index_changed_files(since?)` | How do I reindex just the files changed since a git ref? |
| `scan_repo(verbose?)` | How do I rebuild the whole index? (last resort) |
| `force_index(paths, confirmed?)` | How do I index files that .gitignore excludes? Preview, then confirm with the user |
| `add_directory(path)` | How do I add a sibling repo or folder to the graph? |
| `memory_rescan()` | How do I make a memory file I just edited searchable? |
| `plan_rescan()` | How do I make a plan I just wrote searchable? |

### Diagnostics

| Tool | Question it answers |
|------|---------------------|
| `graph_stats()` | Is the index populated? |
| `live_graph_stats()` | How big and how fresh is the index right now? |
| `indexed_files(pattern?, limit?, path?)` | Is this file indexed, or which files are? |
| `call_stats()` | How has cgh been used in this repo? Includes `by_origin`: agent, hook, cli or internal |
| `findings(file_path?, key_prefix?, severity?, limit?)` | What do cgh's scanners know about these files? |

### Web pages

| Tool | Question it answers |
|------|---------------------|
| `fetch_and_index(url, ttl_hours?, force?)` | How do I make a web page searchable offline? Refused unless `[codegraph] allow_fetch = true`; http/https only, SSRF-guarded |
| `search_fetched(query, limit?)` | What did the pages I fetched say about this? No network |

`cgh fetch --purge [url]` drops fetched content; there is no MCP tool for it.

### Usage log

Every tool call is logged to `.codegraph/call_log.db` with its `origin`: `agent` (an MCP client through `cgh serve`), `cli` (a `cgh` command asking the running owner), `hook` (the same from an agent or git hook) or `internal` (an in-process call). Rows logged before this column existed have no origin and show as `unknown`. `cgh stats`, `cgh logs` and `call_stats()` show the split, so hook traffic is not read as agent choices.

---

[Back to the README](../README.md)
