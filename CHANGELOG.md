# Changelog

All notable changes to **cgh** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The Python import name is `codegraph`; the PyPI package and CLI are `cgh`.

## [Unreleased]

## [0.7.0] - 2026-07-29

### Added
- **cgh-bugreport plugin** (in `plugins/cgh-bugreport`, published
  separately): crash reports that keep the egress promise. Payloads
  are built by allowlist, never by scrubbing: versions, OS, command
  name, exception type and stack frames normalized to cgh's own
  modules; exception messages, paths, arguments and log lines are not
  fields, so they structurally cannot leave, and the PII scanner runs
  over the finished payload as a loud tripwire. Reports spool locally
  (capped, purged, never indexed), `cgh bug preview` prints the exact
  raw payload, and `cgh bug send` is always explicit: it goes through
  the user's own gh CLI to a private repo only (public refused,
  unverifiable refused), dedups by fingerprint (version excluded so
  known crashes stay one issue), confirms the payload first in secure
  mode, and lands in activity.log. The core itself still contains no
  reporting code at all. Incident playbook in
  `docs/BUGREPORT_PLAYBOOK.md`.
- **`cgh init` propagates to federated children.** On a federated
  parent, init now offers (or, with `--yes`, just does) an init of
  every declared subrepo: uninitialized ones get a full init and
  index, initialized ones get the idempotent refresh (Kuzu
  auto-migration, hooks, missing config). Each child runs in its own
  subprocess with the new `--no-children` flag, so propagation stays
  single-level, a federation cycle cannot loop, and one failing child
  never aborts the others. Upgrading cgh on a big monorepo is now one
  `cgh init --yes` at the parent instead of one per subrepo.
- **Guard adapters for Gemini CLI and Codex CLI**, wired against their
  verified hook surfaces. Gemini enforces like Claude: a `BeforeTool`
  hook in `.gemini/settings.json` (matcher on `read_file`,
  `read_many_files`, `run_shell_command`, `search_file_content`,
  `glob`) feeds the same guard handler, exit 2 with a named reason
  denies. Codex is partial by nature: its `PreToolUse` hooks intercept
  shell commands only, so `cgh _hook_guard_codex` answers with the
  stdout JSON decision (both accepted field spellings) and the
  `codex_hooks = true` feature flag is set in `.codex/config.toml`.
  `cgh setup gemini|codex` installs the hooks; `cgh guard status` now
  reports each detected agent from its declared capability.
- **AgentIntegration surface**: the knowledge of each AI tool (detect
  it, install the cgh instructions, wire the guard, declare an honest
  enforcement level) now lives behind one protocol, with the four
  built-ins (Claude Code, Cursor, Codex, Gemini) as its first
  consumers. Plugins add new tools under the `integration` extension
  namespace and `cgh setup` and `cgh guard status` pick them up.
- **Session continuity: checkpoint and resume.** Clearing an agent's
  context stops costing anything. The `checkpoint` MCP tool persists a
  session digest that supersedes the previous one for the same session;
  `resume` returns ONE ranked, budget-capped bundle: standing
  instructions first (never truncated), then recent digests,
  task-relevant knowledge, open plans, and recent file summaries.
  Claude Code lifecycle hooks make it automatic: PreCompact and
  SessionEnd record a checkpoint marker even when the model forgot,
  and SessionStart prints a two-line header announcing the bundle, the
  full bundle loading on demand so it only costs tokens when used.
- **Knowledge store upgrades**: a `standing_instruction` kind for
  durable user rules (they lead every resume bundle), supersede links
  (a new entry can replace an older one, which drops out of searches
  and bundles), read-only federation on request (`scope="all"` on
  `knowledge_search` and `resume` pulls subrepo knowledge,
  scope-tagged, never by default), and `cgh memory review` listing
  stale entries for pruning.
- **Confidentiality guard**: detection is now enforced by default at the
  agent's own tools, not just at cgh's MCP responses. `cgh init` /
  `cgh setup claude` install a Claude Code pre-tool-use hook that
  consults the finding store before every Read, Grep, Glob or Bash call
  and denies access (exit 2 with a named reason) to files flagged
  confidential or carrying block-severity findings, in single-digit
  milliseconds via direct SQLite reads. Bash matching follows the mode:
  `assist` guards known read commands, `secure` denies any command
  whose arguments hit a flagged path. Fail posture follows the mode
  too: assist fails open with a logged warning, secure fails closed. In
  secure mode a second static layer mirrors flagged paths into
  `Read()` deny rules in `.claude/settings.local.json` (synced after
  `cgh classify train` or via `cgh guard sync`, user-authored rules
  never touched). `cgh guard status` reports the honest per-agent map:
  enforce, advisory, or unprotected, where the only barrier is the
  MCP-side gate. Every denial lands in activity.log.
- **cgh-classify plugin** (in `plugins/cgh-classify`, published
  separately): human-trainable confidentiality classification, pure
  standard library. `cgh classify label <file> [--not]` maintains the
  ground truth, `train` fits a TF-IDF + naive Bayes model on it and
  sweeps the repo, `review` lists the files the model is unsure about.
  The safety asymmetry is deliberate: a model prediction can only ever
  block (a predicted-confidential file gets the `confidential` finding),
  while only a human label can clear a file when `mode = "secure"`
  makes the egress gate an allowlist. Labels and model live next to the
  index, machine-local, retraining is instant.
- **cgh-summarize plugin** (in `plugins/cgh-summarize`, published
  separately): prose summaries of indexed files, produced by whatever
  model is already at hand. Backends: the agent CLIs in headless mode
  (`cli:claude` on a light model, `cli:gemini` flash tier, `cli:codex`),
  a local Ollama daemon (the model is one config line), any
  OpenAI-compatible endpoint (vLLM, LM Studio, watsonx, hosted APIs),
  and `structural`, cgh's own outline with no model at all. Third-party
  backends join through the `summarize.backend` extension namespace.
  Before any cloud backend sees a file, the egress gate checks its
  findings: confidential flags, block-severity secrets, and PII (unless
  `allow_pii = true`) stop it; with the new global `mode = "secure"`
  the gate switches to allowlist and only files explicitly labeled
  non-confidential go out. Local backends bypass the gate, every cloud
  call and every denial is logged to activity.log. Files under 4 KB are
  skipped; changed files keep their summary while drift stays under 30%
  of lines across at most 5 changes. Ships `cgh summarize status|run`,
  `cgh insights`, and the `summaries` / `corpus_insights` MCP tools;
  insights batch the gate-cleared summaries into one model call and
  persist the result to the knowledge store.
- **Global `mode` switch** in `[codegraph]`: `assist` (default) or
  `secure`. Secure is assist plus enforcement, nothing turns off; gate
  and guard consumers derive their defaults from it, each overridable.
- **cgh-pii plugin** (in `plugins/cgh-pii`, published separately): every
  indexed file is scanned inline for personal data and credentials.
  Emails, international phone numbers, mod-97-validated IBANs and
  Luhn-validated card numbers become `pii.*` findings (severity warn);
  AWS access keys and PEM private key blocks become `secret.*` findings
  (severity block); hardcoded `password = "..."` assignments are warned
  about. Finding values carry only the match count and first line,
  never the matched data, so the FTS never spreads what the scanner
  detects. Keys can be disabled per repo, and an optional deferred NER
  tier (person names, locations) activates with `cgh-pii[ner]` plus
  `ner = true` under `[plugin.pii]`.
- **cgh-docs plugin** (in `plugins/cgh-docs`, published separately):
  pdf, docx and xlsx parsers. Pages, outline entries, Word headings and
  Excel sheets become document sections, searchable through
  `search_docs`, `doc_outline`, `fts_search` and the federated fan-out
  exactly like markdown. Parsing is best effort: an encrypted pdf or a
  corrupt workbook yields an empty index and a log line, never a failed
  scan. First pip-installable plugin, exercising the loader end to end.
- **Finding store and scanner pipeline**: plugin scanners now run,
  inline ones right after a file is indexed, heavy ones through a
  deferred queue that dedupes by git blob SHA and stays off the watcher
  hot path. Findings (`pii.email`, `secret.aws_key`, `confidential`,
  `summary`, ...) live in `.codegraph/findings.db`, SQLite in WAL mode
  so they stay readable while an owner holds the graph write lock and
  when no owner runs at all. They feed the full-text search (a search
  for "IBAN" surfaces flagged files), are purged with their file, and
  are queryable through the federated `findings` MCP tool and the new
  `cgh findings` command (filters by file, key prefix, severity, scope
  tag per subrepo).
- **Plugin loader**: cgh discovers pip-installed plugins through the
  `cgh` entry point group. A plugin exposes `CGH_PLUGIN_API = 1` and
  `register(api)`; the versioned `PluginAPI` covers parsers (shared
  registry, indexer and watcher pick them up), per-file scanners
  (registered now, invoked once the finding store lands), MCP tools
  (called with the FastMCP instance at owner startup), CLI subcommands,
  and a generic namespaced extension registry so a plugin can extend
  another plugin. `[plugins] enabled / disabled` in config gates
  loading per repo, `[plugin.<name>]` tables pass through to each
  plugin, and the new `cgh plugins` command lists status, version, and
  surfaces. A broken, incompatible, or duplicate plugin is a warning
  and a status line, never a crash.

### Changed
- **License: plugin exception added.** Plugins that interact with cgh only
  through the documented plugin interfaces (the `cgh` entry-point group and
  the public plugin API) are not treated as derivative works and may be
  licensed under any terms, including commercial ones. Using cgh itself
  remains governed by the MIT + CC BY-NC-SA dual license. Groundwork for
  the plugin architecture proposal (docs/proposals/001).

### Fixed
- **Federated children no longer starve the parent's fan-out.** An
  auto-started child owner held its graph write connection forever
  after the first watcher index, and since the graph backend refuses
  cross-process opens while a writer holds the lock, the parent's
  read-only fan-out lost that scope (`db unavailable (locked)`) until
  the child restarted; on a busy monorepo every child ended up locked
  within minutes. The child's owner now releases its write connection
  after each watcher burst when no MCP proxy is attached (reopened
  lazily on the next index or tool call), auto-started children are
  kept alive by a distinct `parent-<pid>` marker that never counts as
  an MCP worker, and the owner's tool connection cache was unified
  into the core connection authority so the release takes effect
  everywhere at once.
- **Flashing console windows on Windows.** A detached owner has no
  console, so every `git.exe` the watcher spawned opened its own
  conhost window; on a busy monorepo that meant a constant stream of
  flashing terminals (measured at ~24 git processes per 30 seconds).
  Every git and ripgrep subprocess now runs with `CREATE_NO_WINDOW`
  on Windows, and the watcher was reworked to batch: one debounce
  timer for the whole event burst and a single
  `git check-ignore --stdin` call for every uncached path, instead of
  one process per file. The dozens-of-git-per-burst pattern collapses
  to one, on every platform.
- **Kuzu migration on installs without the kuzu package**: on a kuzu-less
  install (the default on Python 3.14), the old `graph.db` reads as 0 rows,
  so the migration verifier flagged the fresh DuckDB index as "unexplained
  gains" and refused the swap forever, even with `--force`. The migration
  now detects that the kuzu package is missing, skips the meaningless count
  comparison, and completes the swap with an explicit `kuzu_unreadable`
  status: DuckDB is canonical by construction since it was just rebuilt from
  a full index of the working tree.

## [0.6.0] - 2026-07-24

### Added
- **Federated children auto-start**: when a parent owner starts, it now also
  starts the owner (with watcher) of every initialized subrepo whose owner is
  down, so child indexes stay fresh without running `cgh federate up` by hand.
  Children started this way carry the parent owner's pid as a worker marker
  and stop on their own a few seconds after the parent owner exits. Children
  already up are left untouched, which also keeps two repos federating each
  other from pinning each other alive. Opt out per repo with
  `federate_auto_up = false` under `[codegraph]`.

### Fixed
- **CLI queries crashed on DuckDB repos**: `cgh search`, `cgh lookup`,
  `cgh callers`, `cgh callees`, and `cgh outline` still sent raw Cypher to the
  graph connection, which the DuckDB backend rejects with a ParserException.
  They now go through the backend-neutral GraphDB protocol, so they work on
  DuckDB and Kuzu alike. The crash only showed when no owner was running
  (with an owner up, the CLI silently fell back to FTS), which made federated
  setups look broken whenever the repos' owners were off.
- **CLI queries are now federated**: `cgh search` / `lookup` / `callers` /
  `callees` / `outline` fan out to subrepos like the MCP tools do, tagging
  results with a scope column instead of silently searching the parent only.

## [0.5.0] - 2026-06-08

A large feature release built on a full code audit (security, correctness,
readability, and roadmap). The MCP server now exposes 47 tools, there is a
new CI-oriented CLI command, broader language and framework coverage, and two
optional extras. Everything is additive and backwards compatible; the new
extras are opt-in and defaults are unchanged.

### Added
- **Code-intelligence MCP tools**: `file_summary` (one-shot file orientation),
  `impact_of` (reverse blast radius), `path_between` (shortest call/import
  path), `import_cycles` (SCC cycle detection), `tests_for` / `untested`
  (test-to-code mapping inferred from imports/calls + roles), `hotspots`
  (git churn x import centrality), and `who_knows` (file ownership from git).
- **`role` / `layer` filters** on `search_symbols` and `symbol_lookup`.
- **`cgh impact --since <ref>`**: a non-MCP CLI command for CI and PR bots that
  reports changed symbols, blast radius grouped by role/layer, endpoints
  touched, and tests to run, as a markdown summary or JSON. Reads the graph
  read-only, so no server needs to be running.
- **`cgh graph layers`**: a layer-to-layer dependency diagram (Mermaid/Graphviz).
- **Config-as-data parsers** for JSON / JSONC, YAML, and TOML (top-level keys
  become navigable sections: CI jobs, k8s kinds, compose services,
  package.json scripts, pyproject tables), and a **SQL DDL parser** that turns
  `CREATE TABLE` / `ALTER TABLE` into table sections with columns.
- **More endpoint frameworks**: Django urls, NestJS, Spring, and Gin/Echo, on
  top of the existing FastAPI / Flask / Nuxt / Express.
- **Optional `langs` extra** (`pip install "cgh[langs]"`): C# and Ruby
  tree-sitter parsers, kept optional so the core install stays lean and
  Python-3.14-safe.
- **Optional `lsp` extra** (`pip install "cgh[lsp]"`): opt-in precise
  cross-file CALLS resolution for Python via jedi, behind a `precise_calls`
  config flag (or `CGH_PRECISE_CALLS`).
- **Walk-up root discovery**: `cgh` now resolves the nearest ancestor
  `.codegraph/` from any subdirectory, the way git finds its repo root, so the
  commands work from anywhere inside an initialized project.

### Fixed
- **DuckDB / Kuzu parity**: `purge_file_data` now also removes the inbound side
  of self-referential edges (CALLS, INHERITS) on DuckDB, so `find_callers` no
  longer returns ghost callers after a symbol changes.
- **CALLS resolution** prefers a same-file definition before falling back to
  repo-wide name matching, cutting spurious cross-file edges, and memoizes
  lookups per file.
- The indexer now **honors `max_file_size_kb` and `ignore_patterns`** (they
  were defined and documented but never enforced).
- **Federated subrepos are skipped on Windows.** `is_under_any` left an
  absolute candidate path unresolved and compared case-sensitively, so on the
  case-insensitive Windows filesystem every federated subrepo missed the skip
  list and the parent scanned the whole tree. Paths are now resolved and
  case-normalized on both sides.
- Module-level FTS and `.cghignore` caches are keyed by repo root, so a
  multi-repo process no longer crosses streams.
- `cgh status` shows `would create graph.duckdb` (not the Kuzu file) and
  `Endpoints: unknown` instead of a bare comma when the graph is unreadable.
- Markdown links resolve relative to the file that contains them.
- Barrel re-exports cap their per-import symbol edges; the git-diff discovery
  timeout matches `git ls-files`; `find` prunes ignore dirs at the walk level;
  and several silently-swallowed failures (connection close, query iteration,
  scan deletions) are now surfaced.

### Changed
- The parent + children federation fan-out is now a single shared helper
  (`federate_scoped` / `federate_flat`); the server modules use the canonical
  `_graphdb` names instead of the deprecated `_kuzu` aliases.
- `cmd_init` and `cmd_status` were decomposed into named phase helpers, the
  repeated `--root` argparse boilerplate was factored out, and CLI handlers
  are typed; `cmd_status`'s owner/RO/FTS fallback ladder gained tests.

### Security
- The owner's bearer-token check is now constant-time (`hmac.compare_digest`).
- Removed the dead `.mcp.json` auth env-injection path: the `0600`
  `.codegraph/auth.key` file is the shared secret, and `.codegraph/` is created
  `0700`. Corrected the auth documentation to match.
- `index_changed_files` rejects a `since` ref beginning with `-`, and
  `pattern_search` passes the user pattern after `--` (ripgrep) / via `-e`
  (git-grep), closing argument-injection vectors that could reach ripgrep's
  preprocessor.
- `force_index` refuses absolute paths that resolve outside the repo.
- The generated HTML diagram pins the Mermaid CDN script with an SRI hash.

## [0.4.6] - 2026-06-06

A cross-platform audit pass. Five parallel reviews of signals, paths, file
locking, terminal I/O, and shell handling surfaced a batch of Windows bugs,
all fixed here. `cgh serve`, `cgh reset`, `cgh federate`, and the memory and
plan indexes now work on Windows where they previously crashed or silently
did nothing.

### Fixed
- `cgh federate` on Windows stored subrepo paths with backslashes, which
  produced invalid TOML, so federated subrepos silently read back as none.
  Paths are now stored with forward slashes and the config writer escapes
  backslashes.
- Read-only SQLite opens used a `file:` URI built from a raw path, which is
  invalid on Windows. The FTS fan-out, the `cgh status` fallback, and the
  Read hook hint all silently failed there. Fixed with a portable URI helper.
- Git subprocess output was decoded with the locale codec on Windows, so
  non-ASCII filenames or commit messages produced mojibake or errors. All
  text-mode subprocess and file reads now use UTF-8.
- `cgh reset` ran `pkill`, which does not exist on Windows, and crashed.
- Stopping a process (`cgh serve --stop`, `cgh reset`, `cgh federate down`)
  used raw signals; on Windows that skipped cleanup. A cross-platform
  `terminate()` now handles both worlds. The spawned owner also detaches
  correctly on Windows so `--background` survives the launching shell.
- The Claude memory and plan index used a path slug that was wrong on
  Windows, so it pointed at a directory that never existed and returned no
  results. The slug now matches Claude Code on every platform.
- `.cghignore` patterns with a slash now match on Windows, and `owner.log`
  rotation no longer fails there.

### Changed
- `cgh ensurepath` on Windows prints a safe user-PATH edit instead of `setx`,
  which would duplicate and truncate PATH.

### Docs
- Refreshed the Limitations section: JS/TS imports do resolve to local files
  now (relative, tsconfig aliases, workspace packages); the large-repo timing
  note reflects the DuckDB default.

## [0.4.5] - 2026-06-05

This release focuses on Windows support and install ergonomics.

### Fixed
- `cgh serve` now works on Windows. Three platform breakages were fixed: the
  owner crashed referencing `signal.SIGHUP` (POSIX-only); the liveness probes
  used `os.kill(pid, 0)`, which terminates the target process on Windows
  rather than checking it; and a CRLF-tainted command token like `serve\r`
  was rejected by the argument parser.

### Added
- `python -m cgh` works as an alias for the `cgh` command, handy on Windows
  when the Scripts directory holding `cgh.exe` is not on PATH.
- `cgh ensurepath` adds the directory holding the `cgh` executable to your
  shell PATH (like `pipx ensurepath`). It detects Git Bash, WSL, Linux,
  macOS, and native Windows.
- Git hooks (`post-merge`, `post-checkout`, `post-rewrite`) that run an
  incremental reindex after a pull, merge, branch switch, or rebase, so the
  graph stays fresh when content arrives through git rather than a save.
  `cgh init` installs them, and `cgh hooks install | uninstall | status`
  manages them. cgh will not write into a shared `core.hooksPath` without
  `--shared`.
- The `cgh` banner now also shows on `--version`.

### Changed
- `install.sh` detects the environment (macOS, Linux, WSL, Git Bash), installs
  the correct PyPI package, and offers to fix PATH. A new `install.ps1` does
  the same for native Windows PowerShell.
- CI runs the full 3.11 through 3.14 matrix and a no-em-dashes prose check;
  `uv.lock` is committed and verified on every run.

## [0.4.4] - 2026-06-04

### Fixed
- The error shown when a repo is on the Kuzu backend but the `kuzu` package
  is not installed no longer dumps a Python traceback. It prints a clean
  panel with the reason and the ways to fix it, and re-raises the full stack
  only under `--verbose`. The error has its own `KuzuNotInstalled` type, still
  a `RuntimeError` subclass so existing handlers keep working.
- That same message no longer points at `docs/CONFIGURATION.md`, which is not
  shipped in the wheel, so pip and uv-tool users could not open it. It now
  lists copy-pasteable commands, one per line, including "delete graph.db and
  run `cgh index`" to reindex fresh on DuckDB.

### Changed
- Documentation now states the dual license is conjunctive: MIT **and**
  CC BY-NC-SA 4.0 apply together, not a choice between them. Added a
  `CHANGELOG.md` and a Changelog link in the package metadata.

## [0.4.3] - 2026-06-03

### Fixed
- `cgh federate add` now accepts subrepos indexed on DuckDB. It previously
  rejected them with ".codegraph/ exists but graph.db missing" because the
  CLI gated on the Kuzu file instead of "any graph DB present". The status
  table also reports the backend per subrepo (`ok (duckdb)` / `ok (kuzu)`).
- Federated read-only fan-out degrades gracefully when a Kuzu child repo is
  declared under a parent install that lacks the optional `kuzu` extra,
  instead of raising `ModuleNotFoundError` mid-query.

## [0.4.2] - 2026-06-03

### Added
- Python 3.14 support. `requires-python` no longer carries an upper cap and
  the `Programming Language :: Python :: 3.14` classifier ships in the
  package metadata.

### Changed
- Kuzu is now an optional extra. `pip install cgh` pulls only DuckDB; run
  `pip install cgh[kuzu]` to enable the legacy Kuzu backend. This is what
  unblocks installs on Python 3.14, where Kuzu has no wheels yet. The Kuzu
  imports across `core/db.py` and `core/schema.py` are lazy, and selecting
  the Kuzu backend without the extra installed raises a clear error pointing
  at `pip install cgh[kuzu]` or `cgh migrate-to-duckdb`.

## [0.4.1] - 2026-06-03

This is the first published release of the 0.4 line (0.4.0 was withdrawn from
PyPI and its version number is permanently blocked there).

### Added
- DuckDB graph backend, selectable via `CGH_DB=duckdb` or auto-detected from
  the files on disk (`graph.duckdb` -> DuckDB, `graph.db` -> Kuzu).
- `cgh migrate-to-duckdb` command: re-indexes a Kuzu repo into DuckDB,
  verifies node/edge counts, and optionally deletes the old `graph.db`.
- Backend row in `cgh status` showing which graph backend is active.
- Backend-neutral federation: a parent can fan out read-only queries across
  child subrepos running a mix of Kuzu and DuckDB.

### Changed
- **DuckDB is now the default graph backend.** Fresh repos index into
  `graph.duckdb`; existing Kuzu repos are auto-migrated to DuckDB on the next
  `cgh init`. DuckDB is roughly 2x smaller on disk and indexes substantially
  faster than Kuzu on the same source.
- The whole graph layer was ported behind `GraphDB` / `QueryResult` protocols
  so backends are swappable: indexer, query tools, arch/docs/dead-code tools,
  viz, CLI stats, and federation all run backend-neutral.

### Fixed
- `cgh init` no longer crashes mid-index when a read-only connection is
  already cached: DuckDB rejects a same-file RO + RW pair in one process, so
  the cached RO connection is now closed before opening RW.
- The migrate verifier tolerates known "stale Kuzu" signatures (IMPORTS edges
  going from 0 to N, or any metric where DuckDB <= Kuzu from ghost rows left
  by deleted files) and accepts DuckDB as canonical instead of bailing.

## [0.4.0] - 2026-05-31 (withdrawn)

Never successfully published to PyPI; the version is permanently blocked
there after the upload was deleted. Its contents shipped in 0.4.1 and later.
Highlights from this line:

### Added
- Go, Rust, and Java tree-sitter parsers.
- TypeScript path-alias resolution from `tsconfig.json`.
- npm / pnpm / yarn workspace package import resolution.
- `cgh status` shows the installed cgh version.

### Changed
- Repository restructured: the top of `codegraph/` is now three files
  (`__init__.py`, `__main__.py`, `indexer.py`) with everything else grouped
  into subpackages (`core/`, `parsers/`, `imports/`, `state/`, `analysis/`,
  `server/`, `cli/`, ...).

### Fixed
- IMPORTS edges are actually written to the graph (they were computed but
  never persisted).
- Identifiers are NFKC-normalized and the call filter is Unicode-aware.
- Parse errors are handled robustly and bad files are skipped cleanly.
- CALLS edges to language builtins are skipped.

## [0.3.1] - 2026-05-29

### Added
- Claude Code `PreToolUse` hooks for Grep and Read, plus `cgh doctor` to
  audit the Claude Code integration for drift.
- `cgh index` routes through a running owner via MCP when one is alive.

### Fixed
- Python capped to `<3.14` until Kuzu ships cp314 wheels (lifted again in
  0.4.2 once Kuzu became optional).

## [0.3.0] - 2026-05-17

First tagged release on PyPI.

[Unreleased]: https://github.com/altikva/cgh/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/altikva/cgh/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/altikva/cgh/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/altikva/cgh/compare/v0.4.6...v0.5.0
[0.4.6]: https://github.com/altikva/cgh/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/altikva/cgh/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/altikva/cgh/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/altikva/cgh/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/altikva/cgh/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/altikva/cgh/compare/v0.3.1...v0.4.1
[0.4.0]: https://github.com/altikva/cgh/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/altikva/cgh/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/altikva/cgh/releases/tag/v0.3.0
