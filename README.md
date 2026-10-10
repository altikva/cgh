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

**Local code graph and shared memory for AI coding assistants.**

Parses your repo into a graph of files, functions, classes, Terraform resources, and Markdown documentation -- then exposes it as an MCP server so Claude Code, Cursor, Codex, Gemini, and IBM Bob can do symbol-level lookups instead of reading entire files. On top of the graph: a knowledge and session memory every connected agent shares, and scanner findings (PII, secrets, confidentiality labels) that plugins check before sending a file to a cloud model.

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

`cgh[plugins]` and `cgh[full]` bring cgh-docs, cgh-codegen and cgh-bugreport.
cgh-pii (on-demand secret scanning, PII redaction) and cgh-vision (image
understanding) install by name; see [docs/PLUGINS.md](docs/PLUGINS.md).

Upgrading from 0.14 or older? Read
[docs/UPGRADING-0.15.md](docs/UPGRADING-0.15.md) first: cgh no longer blocks
agent file access, the default plugin set is smaller, and the upgrade
command needs `-U`.

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
Your source files (.py / .ts / .java / .go / .rs / .vue / .tf / .md)
    ^
File watcher (watchdog)         <-- live incremental updates on save
```

Instead of reading `services.py` (800 tokens) to find where `verify_token` is defined, your AI calls `symbol_lookup("verify_token")` and gets back the file, the line range, the kind and the docstring, then reads only those lines.

## Languages

Six languages get a full symbol graph, functions and classes and the calls
between them, with their imports resolved into `File -> File` edges:

| | | imports resolved |
|---|---|---|
| Python | `.py .pyw` | yes |
| TypeScript, JavaScript | `.ts .tsx .js .jsx .mjs .cjs` | yes |
| Java | `.java` | yes |
| Go | `.go` | yes |
| Rust | `.rs` | yes |
| Vue SFC | `.vue` | yes |

C# and Ruby parse too, behind `pip install "cgh[langs]"`.

Alongside them, Markdown becomes a heading tree with its links and code
references, Terraform yields resources, variables and outputs, and JSON,
TOML, YAML and SQL expose their structure as sections. Every one of them is
searchable and reachable from the graph.

---

## Documentation

| Guide | What it covers |
|---|---|
| [Install](docs/INSTALL.md) | one-line installers, extras, corporate mirrors, PATH |
| [Upgrading to 0.15](docs/UPGRADING-0.15.md) | what changed for your agent, the upgrade command, old findings, rollback |
| [Upgrading to 0.16](docs/UPGRADING-0.16.md) | the one-time re-parse, what refuses until then, federation and seeds, removed plugins, rollback |
| [CLI reference](docs/CLI_REFERENCE.md) | every verb and flag |
| [Configuration](docs/CONFIGURATION.md) | `config.toml`, environment variables, `.cghignore` |
| [MCP tools](docs/MCP_TOOLS.md) | the tools your agent calls, by category |
| [Integrations](docs/INTEGRATIONS.md) | Claude Code, Cursor, Codex, Gemini, IBM Bob |
| [Federation](docs/FEDERATION.md) | one parent repo querying its sub-repos read-only |
| [Session memory](docs/MEMORY.md) | knowledge and plans that survive a context clear |
| [Security](docs/SECURITY.md) | what can leave the machine, findings, the egress gate, the MCP auth key |
| [Plugins](docs/PLUGINS.md) | installing them, disabling them, writing one |
| [Parsers](docs/PARSERS.md) | the parser interface and how to add a language |
| [Graph schema](docs/SCHEMA.md) | the nodes and edges the index holds |
| [Embedding (SDK)](docs/EMBEDDING.md) | using cgh as a library |

---

## Limitations

- **CALLS resolution is name-based by default, guided by imports.** For Python and TS/JS a call follows its shape: a same-file definition wins, then the file an import names (`from lib import f`, `import * as m`), `self.f()` the class and its bases, `obj.f()` on a local or attribute whose class is known (built, annotated or returned by an annotated factory) that class, and `obj.f()` on an object of unknown type only the few methods of that name (or one in a file the caller imports). Calls into third-party modules and from production code into test files get no edge. Other languages link to a same-file function of that name, else to every function with that name outside test files. Cross-file edges stay best-effort: without types, `obj.f()` can miss its target or pick a look-alike. For Python you can opt into precise resolution with `pip install cgh[lsp]` and `precise_calls = true` (jedi-backed).
- **Terraform is read file by file, not evaluated.** Blocks, references, module inputs and `moved`/`import`/`removed` are parsed with a real HCL grammar, but nothing is planned: `count`/`for_each` instances, dynamic blocks and computed addresses stay unexpanded, and a remote module is only read when `[terraform] module_sources` maps it to a local checkout.
- **Imports resolve to files in your repo, never to dependencies.** Python, JS/TS, Vue, Java, Go and Rust each map an import onto the file it names, following that language's own layout rules. Anything outside the repo stays unresolved on purpose: the standard library, a Go module you do not own, an external crate or npm package gets no node and no edge, because inventing one would be a lie about your code. Cross-repo edges are not inferred either, each federated scope is canonical for its own files.
- **Markdown code refs are heuristic.** PascalCase and snake_case patterns are matched, so a ref can be a false positive.
- **Large repos take minutes to index.** Incremental updates stay fast (well under a second per changed file), and a pull or merge reindexes only the changed files via the git hooks.

---

## License

Dual-licensed under MIT **and** CC BY-NC-SA 4.0: both licenses apply together and you must comply with both. In practice that means non-commercial use, share-alike derivatives, attribution, and no warranty. Copyright (c) 2026 ALTIKVA. See [LICENSE](./LICENSE) or the canonical notice at https://www.altikva.com/licenses/LICENSE-1.0.

**Plugin exception**: a plugin that talks to cgh only through the documented plugin interfaces (the `cgh` entry-point group and the public plugin API) is not treated as a derivative work and may be licensed under any terms its author chooses, including commercial ones. Using cgh itself stays under the dual license whatever plugins are installed. Full wording in [LICENSE](./LICENSE).
