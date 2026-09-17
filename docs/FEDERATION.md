# Federation

When you work in a parent folder that holds several sub-projects, each with its own `.git` and its own `.codegraph/` index, you don't want the parent to re-index everything. Per-child `.gitignore` semantics get lost, large trees (node_modules, vendor) get walked, duplicate work explodes. The federation model fixes this:

- The parent **only indexes files outside any declared subrepo** (its README, top-level configs, cross-repo docs).
- Each subrepo keeps its own `.codegraph/` as the canonical index for its own code.
- At MCP query time, the parent **fans out read-only queries** to each child's DB and aggregates results, tagging every hit with a `scope` field (`parent` or the child's basename).

### Setup

```bash
# In each subrepo (one-time)
cd apps/api && cgh init && cgh index

# In the parent
cd ../..
cgh init                                   # auto-detects nested .codegraph/, offers to federate
cgh federate add ./apps/api ./apps/web     # (or declare manually)
cgh federate list                          # status + owner state per child
cgh index                                  # parent indexes only its own files
cgh serve --background --watch             # parent owner federates queries to children
                                           # and auto-starts each child's owner + watcher
cgh federate up                            # optional: children that OUTLIVE the parent owner
```

### What's federated

| MCP tool | Behavior |
|---|---|
| `symbol_lookup`, `search_symbols`, `find_callers`, `find_callees` | Concat results, each tagged with `scope` |
| `imports_of`, `subgraph` | Concat. Cross-repo IMPORTS edges are NOT inferred (each scope's graph is canonical for its own files) |
| `pattern_search` | Runs ripgrep in each scope's tree |
| `fts_search` | Concat then sort by score (BM25 not renormalized across repos) |
| `search_docs`, `doc_outline`, `doc_refs` | Concat |
| `architecture_overview` | Returns `{by_scope: {parent: {...}, child1: {...}}}` when subrepos are present |
| `domain_map`, `endpoints` | Concat with per-result scope tag |
| `find_dead_code` | **Per-scope analysis**. A symbol "dead" in scope X may be called from scope Y. The response carries an explicit `note` field reminding you not to delete blindly. |

### What's NOT federated

`knowledge_*`, `memory_*`, `plan_*`, all write-side tools (`index`, `force_index`, `incremental_reindex`, `add_directory`), and `context_for_task` stay parent-local. Each project keeps its own knowledge / memory / plans store.

### Resilience

If a child's DB is locked or unavailable (its own owner is mid-write, the child got deleted from disk), the response carries `partial: true` and `warnings: [{scope, error}]`. Results from other scopes still flow. Re-query in a moment if you need full coverage.

Owners are independent: the parent reads child DBs directly as files, it does NOT auto-spawn child owners. Use `cgh federate up` to ensure every child has its own watcher running, or accept that a child without a live owner may serve slightly stale data.

---

---

[Back to the README](../README.md)
