<p align="center">
  <img src="https://raw.githubusercontent.com/altikva/cgh/main/assets/img/cgh-cli.svg" alt="The cgh CLI landing screen: banner, command list, and examples" width="820">
</p>

<p align="center">
  <a href="https://pypi.org/project/cgh/"><img src="https://img.shields.io/pypi/v/cgh?color=3775A9&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/cgh/"><img src="https://img.shields.io/pypi/pyversions/cgh" alt="Python versions"></a>
  <a href="https://www.npmjs.com/package/@altikva/cgh"><img src="https://img.shields.io/npm/v/%40altikva%2Fcgh?color=CB3837&label=npx" alt="npx"></a>
  <a href="https://github.com/altikva/cgh/actions/workflows/ci.yml"><img src="https://github.com/altikva/cgh/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT%20%26%20CC%20BY--NC--SA-3DA639" alt="License: MIT and CC BY-NC-SA"></a>
</p>

**Local code graph, shared memory and guardrails for AI coding assistants.**

Parses your repo into a graph of files, functions, classes, Terraform resources, and Markdown documentation -- then exposes it as an MCP server so Claude Code, Cursor, Codex, Gemini, and IBM Bob can do symbol-level lookups instead of reading entire files. On top of the graph: a knowledge and session memory every connected agent shares, and a confidentiality layer (findings, egress gate, per-agent guard hooks) that decides what an agent may read and what may reach a cloud model.

**Result:** 40-60% fewer context tokens on typical navigation tasks, learnings that survive context clears, and nothing leaving the machine without a gate.

```bash
pip install cgh && cgh init && cgh serve
```

---

## Why cgh: the measured gains

cgh's job is to keep an agent's working context small and its round-trips few. It answers code questions from the graph, returning exact `file:line`, instead of the agent reading whole files or grepping. That shows up as fewer context tokens and fewer turns, at equal correctness.

The figures below come from a two-arm benchmark: the same tasks run twice against the same repo, once with cgh available and once with Read/Grep only, scored on each session's token usage, turn count, and answer correctness. Cost is compared only across tasks both arms got right, so a cheap wrong answer never reads as a saving.

**On code-navigation tasks: about 40 to 60% fewer context tokens and 20 to 40% fewer turns, correctness unchanged.**

The gap is widest on multi-file questions, where a graph beats text search. For "what breaks if I change `_backend`?", the agent has to follow call edges across a module:

| | turns | context tokens |
|---|---|---|
| Read / Grep | 13 | 2607 |
| cgh | 6 | 886 |

That question is one command:

<p align="center">
  <img src="https://raw.githubusercontent.com/altikva/cgh/main/assets/img/cgh-callers.svg" alt="cgh callers: the call graph of resolve_import rendered as a tree with exact file and line" width="820">
</p>

**Delegating writes.** The [`cgh-codegen`](plugins/cgh-codegen) plugin does the same for writes: predictable, pattern-following code (tests, stubs, config, boilerplate) is handed to a cheap or local model that mirrors an existing file, and the reference never enters the primary model's context. cgh picks the file to mirror from the graph, so selecting it costs zero model tokens.

| task | reference size | primary-model tokens (without -> with) |
|---|---|---|
| generate an auth-stripping test suite | 108KB test file | ~27,980 -> ~100 (99.6%) |
| generate a `models.pyi` type stub | 41KB module | ~14,000 -> ~100 (99.3%) |

**Per task, what the agent does instead of reading files:**

| Task | Without cgh | With cgh |
|------|-------------|----------|
| Find where `process_data` is defined | Read 3-5 files (~2,000 tokens) | `symbol_lookup` (< 50 tokens) |
| Find all callers of `save_record` | Read every candidate file | `find_callers` (< 50 tokens) |
| Understand blast radius of `utils.py` | Read imports manually | `subgraph` (< 100 tokens) |
| Find docs about reconciliation | Read all `.md` files | `search_docs` (< 50 tokens) |
| Build context for a task | 5-10 file reads (~5,000 tokens) | `context_for_task` (< 200 tokens) |

**What this is not.** The billed cost, once the model's prompt cache is counted, is roughly a wash on the read side: the cache dominates the invoice, so fewer turns do not cut it much. cgh's gain there is a smaller working context and fewer turns, not a smaller bill. On a trivial one-file edit cgh adds nothing. Run-to-run variance is real (around 20%), so read these as ratios over a task set rather than a single guaranteed number.

---

## Install

```bash
pip install cgh                 # or: pipx install cgh / uv tool install cgh
pip install "cgh[full]"         # plugins, extra language parsers, precise Python calls
```

No Python? Run the standalone binary through npm, or download it from the
[latest release](https://github.com/altikva/cgh/releases/latest):

```bash
npx @altikva/cgh serve          # fetches the binary for your OS, verifies it, runs it
npx @altikva/cgh --egress serve # the egress build, with the model-calling plugins
```

The binary uses the SQLite backend; `uvx cgh` bundles DuckDB and every plugin.
One-line installers for macOS, Linux, WSL, Git Bash and Windows PowerShell, corporate mirror
settings, optional extras and the `cgh: command not found` fix are in
**[docs/INSTALL.md](docs/INSTALL.md)**. Python 3.11 through 3.14.

## Quick start

```bash
# 1. Initialize (interactive wizard)
cgh init

# 2. Build the graph
cgh index

# 3. Check what was indexed
cgh stats

# 4. Start the MCP server for your AI tool
cgh serve --watch --reindex
```

`cgh status` tells you what the graph holds and whether it still matches the working tree:

<p align="center">
  <img src="https://raw.githubusercontent.com/altikva/cgh/main/assets/img/cgh-status.svg" alt="cgh status: backend, owner, scan freshness, import coverage and file count" width="820">
</p>

## How it works

```
AI Assistant (Claude / Cursor / Codex / Gemini / IBM Bob)
    |  symbol_lookup("process_data")
    |  search_docs("reconciliation")
    |  context_for_task("fix auth bug")
    v
MCP server (codegraph)          <-- stdio, no network
    |  SQL graph query + BM25 FTS
    v
DuckDB graph DB (.codegraph/graph.duckdb)   <-- embedded, file-based
SQLite FTS5 (.codegraph/fts.db)       <-- BM25 full-text search
    |  indexed from
    v
Your source files (.py / .ts / .tf / .md / .vue)
    ^
File watcher (watchdog)         <-- live incremental updates on save
```

Instead of reading `services.py` (800 tokens) to find where `verify_token` is defined, your AI calls `symbol_lookup("verify_token")` and gets back the file, the line range, the kind and the docstring, then reads only those lines.

---

## Documentation

| Guide | What it covers |
|---|---|
| [Install](docs/INSTALL.md) | one-line installers, extras, corporate mirrors, PATH |
| [CLI reference](docs/CLI_REFERENCE.md) | every verb and flag |
| [Configuration](docs/CONFIGURATION.md) | `config.toml`, environment variables, `.cghignore` |
| [MCP tools](docs/MCP_TOOLS.md) | the tools your agent calls, by category |
| [Integrations](docs/INTEGRATIONS.md) | Claude Code, Cursor, Codex, Gemini, IBM Bob |
| [Federation](docs/FEDERATION.md) | one parent repo querying its sub-repos read-only |
| [Session memory](docs/MEMORY.md) | knowledge and plans that survive a context clear |
| [Security](docs/SECURITY.md) | findings, secure mode, the guard, the MCP auth key |
| [Plugins](docs/PLUGINS.md) | installing them, disabling them, writing one |
| [Parsers](docs/PARSERS.md) | the parser interface and how to add a language |
| [Graph schema](docs/SCHEMA.md) | the nodes and edges the index holds |
| [Embedding (SDK)](docs/EMBEDDING.md) | using cgh as a library |

---

## Limitations

- **CALLS resolution is name-based by default.** A call is linked to a same-file function of that name, falling back to all repo functions with that name only when there is no same-file match, so cross-file call edges are best-effort. For Python you can opt into precise cross-file resolution with `pip install cgh[lsp]` and `precise_calls = true` (jedi-backed); other languages stay name-based.
- **Terraform HCL uses regex, not a full grammar.** Complex meta-arguments may be missed.
- **JS/TS imports resolve to local files only.** Relative imports, tsconfig `paths` aliases, `~/` and `@/` conventions, and workspace packages do create a `File -> File` IMPORTS edge. Bare external packages are not resolved to a node, and cross-repo edges are not inferred.
- **Markdown code refs are heuristic.** PascalCase and snake_case patterns are matched, so a ref can be a false positive.
- **Large repos take minutes to index.** Incremental updates stay fast (well under a second per changed file), and a pull or merge reindexes only the changed files via the git hooks.

---

## License

Dual-licensed under MIT **and** CC BY-NC-SA 4.0: both licenses apply together and you must comply with both. In practice that means non-commercial use, share-alike derivatives, attribution, and no warranty. Copyright (c) 2026 ALTIKVA. See [LICENSE](./LICENSE) or the canonical notice at https://www.altikva.com/licenses/LICENSE-1.0.

**Plugin exception**: a plugin that talks to cgh only through the documented plugin interfaces (the `cgh` entry-point group and the public plugin API) is not treated as a derivative work and may be licensed under any terms its author chooses, including commercial ones. Using cgh itself stays under the dual license whatever plugins are installed. Full wording in [LICENSE](./LICENSE).
