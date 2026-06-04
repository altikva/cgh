# Changelog

All notable changes to **cgh** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The Python import name is `codegraph`; the PyPI package and CLI are `cgh`.

## [Unreleased]

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

[Unreleased]: https://github.com/altikva/cgh/compare/v0.4.4...HEAD
[0.4.4]: https://github.com/altikva/cgh/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/altikva/cgh/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/altikva/cgh/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/altikva/cgh/compare/v0.3.1...v0.4.1
[0.4.0]: https://github.com/altikva/cgh/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/altikva/cgh/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/altikva/cgh/releases/tag/v0.3.0
