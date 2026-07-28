# Proposal 001: plugin architecture for cgh

Status: accepted 2026-07-29 (all open questions resolved). Implementation
has not started yet.

## Why

Three feature families are on the table: document parsing (pdf, docx, xlsx),
PII detection during indexing, and a human-trainable confidentiality
classifier. None of them belongs in the core install: they pull heavy
dependencies (pdf libs, NER models, sklearn), they broaden cgh's scope beyond
the code graph, and different users want different subsets. A plugin
mechanism lets each ship as its own pip package while the core stays lean.

The guiding frame: cgh is the local layer that knows what an AI agent may
read and send. Plugins extend what cgh can read (new parsers), what it knows
about each file (scanners: PII, secrets, confidentiality), and what it can do
(new MCP tools, new CLI verbs).

## Goals

- `pip install cgh-pdf` and the next `cgh index` parses PDFs. No config
  edit required, no core release required.
- A plugin can add: file parsers, per-file scanners, MCP tools, CLI
  subcommands.
- A broken or incompatible plugin degrades to a warning, never a crash.
- The contract is explicit and versioned from day one.

## Non-goals (v1)

- No sandboxing. A plugin is arbitrary Python executed in the owner process,
  same trust level as cgh itself. Installing one is the consent.
- No plugin marketplace, no remote fetching. Distribution is pip.
- No hot reload. Plugins load at process start; restart the owner to pick up
  a newly installed plugin.
- No replacement of built-in behavior (overriding how `.py` is parsed). v1
  is additive only; an override story can come in v2 once the contract has
  proven stable.

## What exists today (and what we reuse)

| Surface | Today | Reused as |
|---|---|---|
| Parsers | `@register_parser(".rs")` decorator + import-time discovery in `codegraph/parsers/` | The exact same decorator, called from plugin code |
| MCP tools | One `register(mcp)` function per `server/tools_*.py` module | The same shape, plugins get the FastMCP instance |
| CLI | Central argparse in `__main__.py` | Plugins get the subparsers object |
| Config | `.codegraph/config.toml`, `[codegraph]` table | New `[plugins]` table + one table per plugin |

The parser registry is already a plugin system in miniature; the work is
opening it to code that lives outside the `codegraph` package, and adding
the scanner surface that does not exist yet.

## Discovery

Standard Python entry points, group `cgh`:

```toml
# pyproject.toml of a plugin
[project.entry-points.cgh]
pdf = "cgh_pdf.plugin"
```

At load time cgh iterates `importlib.metadata.entry_points(group="cgh")`,
imports each module, checks the API version, and calls its `register`
function. No filesystem scanning, no config listing: installed means
discoverable. Config can then disable:

```toml
[plugins]
# disabled = ["pdf"]        # skip a plugin without uninstalling it
# enabled = ["pdf", "pii"]  # allowlist mode: load ONLY these
```

Per-plugin settings live in their own table, passed through verbatim:

```toml
[plugin.pii]
ner = false          # regex tier only
```

## The contract

A plugin module exposes two names:

```python
CGH_PLUGIN_API = 1

def register(api: "PluginAPI") -> None:
    ...
```

`PluginAPI` is a small facade owned by cgh, the only supported way in:

```python
class PluginAPI:
    plugin_name: str          # entry point name, e.g. "pdf"
    repo_root: Path | None    # None in repo-less contexts (cgh parsers)
    config: dict              # the [plugin.<name>] table, verbatim

    def register_parser(self, *extensions: str):
        """Same decorator as codegraph.parsers.register_parser.
        The parser subclasses BaseParser and returns a FileIndex."""

    def register_scanner(self, scanner: "FileScanner") -> None:
        """Post-parse hook, see Scanners below."""

    def register_mcp_tools(self, fn: "Callable[[FastMCP], None]") -> None:
        """fn is called with the FastMCP instance at owner startup,
        exactly like the internal tools_*.py register() functions.
        Ignored outside the owner process."""

    def register_cli(self, fn: "Callable[[argparse._SubParsersAction], None]") -> None:
        """fn may add subcommands. Called during CLI dispatch."""
```

Everything a parser produces goes through the existing `FileIndex`
dataclass, so plugin output lands in the graph, the FTS, and the federated
fan-out with zero special-casing. A PDF parser is just a parser whose
`FileIndex` contains `sections` instead of `functions`.

### Scanners (the new surface)

Scanners are what PII detection and the confidentiality classifier need:
look at a file's content after parsing, attach findings to the graph.

```python
@dataclass
class ScanFinding:
    key: str          # namespaced, e.g. "pii.email", "secret.aws_key"
    value: str        # summary value, e.g. the count or the label
    line: int = 0     # 0 = file-level finding
    severity: str = "info"   # "info" | "warn" | "block"

class FileScanner(Protocol):
    name: str                    # e.g. "pii-regex"
    deferred: bool               # True = run off the hot path (see below)
    def scan(self, path: Path, text: str, index: FileIndex) -> list[ScanFinding]: ...
```

Pipeline placement: `index_file` runs inline scanners right after
`parser.parse()` and stores findings; `deferred=True` scanners are queued
and run by a low-priority thread in the owner, keyed by blob SHA so a file
is never re-scanned unchanged. The watcher hot path stays fast: regex-tier
PII can be inline, NER must be deferred.

Storage: one new `Finding` node table (file_path, scanner, key, value,
line, severity, blob_sha) plus a `HAS_FINDING` edge from File. Query surface
ships with core, not with plugins: a `findings(file?, key_prefix?)` MCP tool
and `cgh findings` CLI verb, both federated with the usual `scope` tag.
Whichever scanner wrote them, findings are queryable the same way.

### Loading points

- **Owner startup** (`owner_main`): full load, all four surfaces. This is
  the main home: scanners run here, MCP tools live here.
- **CLI dispatch** (`__main__.main`): parsers + CLI surfaces load so
  `cgh index` and plugin verbs work without an owner.
- **Proxy** (`cgh serve` bridge): no plugin loading at all. It forwards
  JSON-RPC and must stay dumb.

Load order: builtins first, then plugins in entry-point order. v1 is
additive, so ordering conflicts reduce to "two plugins claim the same
extension"; first registration wins and the loser is logged.

## Versioning

- `codegraph.plugin_api.API_VERSION = 1`, exported constant.
- A plugin declares `CGH_PLUGIN_API = 1`. Mismatch = skip + warning, never
  a crash.
- Additive evolution (new optional PluginAPI methods) keeps the number.
  Any breaking change (signature change, removed hook, FileIndex field
  removal) bumps it. The changelog gets a "Plugin API" section the day the
  first external plugin exists.
- `BaseParser`, `FileIndex`, `ScanFinding`, `PluginAPI` become public API
  the moment this ships. They get docstring-level stability notes and
  tests that pin their shape.

## Failure isolation

Same philosophy as `_logged_tool`: plugin failures are logged, never fatal.

- Import error or API mismatch at load: warning to stderr/owner.log, plugin
  skipped, `cgh plugins` shows it as broken with the reason.
- Parser raising during `parse()`: the file is skipped, identical to how
  built-in parser errors are handled today.
- Scanner raising: findings for that scanner are dropped for that file,
  error logged to activity.log, indexing continues.
- MCP tool raising: already wrapped by FastMCP + `_logged_tool`.

## Introspection

`cgh plugins` lists what loaded: name, version (from package metadata),
API version, surfaces registered, status (active, disabled, broken +
reason). The bare `cgh` landing screen gains one line per active plugin.

## Trust model

A plugin runs with cgh's privileges inside the owner: it can read the repo,
the graph, `.codegraph/auth.key`, and reach the network. There is no
sandbox and v1 does not pretend otherwise. The documented rule: install
plugins you trust, pin versions, review before upgrading, use
`[plugins] enabled = [...]` allowlist mode on sensitive repos. This is the
same trust model as pytest, flake8, or any entry-point ecosystem, and it
must be stated plainly in the README.

## Licensing

Decided (2026-07): the LICENSE carries a plugin exception. A plugin that
interacts with cgh solely through the documented plugin interfaces (the
`cgh` entry-point group and the public plugin API) is not treated as an
adaptation or derivative work and may be licensed under any terms its
author chooses, commercial included. The exception covers plugin licensing
only: copying or modifying cgh source, distributing cgh, and using cgh
itself all stay under the MIT + CC BY-NC-SA dual license. See the
"Additional permission: plugin exception" section of LICENSE.

## First-party plugins (the actual roadmap)

The three feature families from the discussion become the reference
implementations, in dependency order:

1. **cgh-docs**: pdf (pypdf), docx (python-docx), xlsx (openpyxl) parsers
   producing `sections`/`resources`. Proves the parser surface.
2. **cgh-pii**: regex tier inline (emails, phones, IBAN, cards, secrets),
   optional NER tier (Presidio/spaCy) as deferred scanner behind
   `[plugin.pii] ner = true`. Proves the scanner surface + findings store.
3. **cgh-classify**: confidentiality labeling (`cgh classify label <file>`),
   TF-IDF + logistic regression trained on local labels, uncertain files
   surfaced for human review, `confidential` finding written per file.
   Proves the CLI surface and consumes pii findings as features.

Core keeps: the plugin loader, the Finding store + query tools,
`cgh plugins`. Everything else lives in the plugins.

## Implementation sketch (core side)

- `codegraph/plugin_api.py`: `API_VERSION`, `PluginAPI`, `ScanFinding`,
  `FileScanner` protocol. ~150 lines.
- `codegraph/plugins.py`: discovery, load, disable/enable logic, registry
  of loaded plugins. ~150 lines.
- `core/schema*`: `Finding` table + `HAS_FINDING` edge, both backends.
- `indexer.py`: scanner invocation in `index_file` + deferred queue in the
  owner. The queue reuses the blob-SHA machinery from scan_meta.
- `server/tools_findings.py`, `cli/commands_plugins.py`, `cli/commands_findings.py`.
- Tests: a fixture plugin package installed into the test env exercising
  all four surfaces, plus failure-isolation tests (broken import, raising
  scanner, wrong API version).

Estimated as three PRs: (1) loader + parser/CLI surfaces + `cgh plugins`,
(2) scanner surface + Finding store + MCP/CLI query tools, (3) cgh-docs as
a separate repo/package validating the whole chain end to end.

## Amendment 2026-07-29: generic registry and agent integrations

Two additions from the proposal 002 discussion, both part of API v1.

**Namespaced extension registry.** `PluginAPI` gains one generic method:

```python
def register_extension(self, namespace: str, obj: object) -> None: ...
```

plus a read side (`get_extensions(namespace)`) available to core and to
other plugins. This is how a plugin extends another plugin (proposal 002
uses `summarize.backend` for summarizer models) without core learning
any domain vocabulary. Namespaces are plain dotted strings, first come
first served, documented by whoever consumes them.

**Agent integrations as a pluggable surface.** Today the knowledge of
each AI tool (detect it, write its MCP server registration, inject the
cgh instructions into its markdown, install skills or hooks) is
hardcoded in `codegraph/integrations/` for Claude Code, Cursor, Codex,
and Gemini. A new agent CLI shipping tomorrow must be addable by plugin,
not by core release. An integration is a descriptor registered under the
`integration` namespace:

```python
class AgentIntegration(Protocol):
    name: str                      # "bob", shown by cgh init / cgh setup
    def detect(self) -> bool: ...  # binary on PATH, config dir present
    def mcp_config(self, repo_root: Path) -> ConfigSpec: ...
        # where and in which format to register the cgh MCP server
    def instructions(self, repo_root: Path) -> InstructionSpec: ...
        # target markdown (BOB.md, AGENTS.md, rules dir) + marker block
        # to inject and update idempotently
    # optional: skills_dir(), hooks() for tools that support them
```

`cgh init` detection and `cgh setup <name>` iterate builtins plus the
registry; `cgh setup` accepts plugin-provided names. The four built-in
integrations get refactored onto this same protocol, which keeps the
contract honest: core is just the first consumer of its own surface.
Since many recent tools converge on an AGENTS.md file plus a standard
MCP JSON block, a plugin for such a tool is close to pure data (paths
and formats), a few dozen lines.

## Decisions (all questions resolved 2026-07-29)

1. **Naming**: first-party plugins are `cgh-docs` / `cgh-pii` /
   `cgh-classify` on PyPI, import names `cgh_docs` / `cgh_pii` /
   `cgh_classify`. The `cgh-` PyPI prefix is reserved for ALTIKVA
   first-party plugins; third parties are free to publish but encouraged
   toward `<name>-cgh` or their own naming.
2. **Findings feed the FTS**: yes. A full-text search for "IBAN" or
   "confidential" surfaces flagged files through the search tools agents
   already use, not only through the dedicated `findings` tool.
3. **Federation**: reads federate (children's findings come back
   scope-tagged through the usual fan-out); writes stay local (each repo
   scans itself with its own `[plugin.*]` config, the parent never writes
   into a child).
4. **License**: plugin linking exception added, see the Licensing section
   above.

## Future surfaces (v2 candidates, out of scope for v1)

- **Lifecycle hooks**, in the Claude Code sense: user-configured commands
  on events (`pre_index`, `post_index`, `pre_tool_response`), declared in
  `.codegraph/config.toml` (machine-local, never committed). Complementary
  to plugins: a hook is a per-repo shell one-liner, a plugin is packaged
  and distributable. The highest-value one is `pre_tool_response`
  filtering/redaction driven by findings (block or redact content flagged
  `confidential` or `pii.*` before it reaches the agent), which needs the
  Finding store to exist first. cgh already ships git reindex hooks, so
  the concept has precedent in the codebase.
- **Local-model plugins**: `cgh-embed` (static embedding model, ~30 MB
  CPU-only, powers semantic search and gives the classifier real
  features) and `cgh-summarize` (small quantized LLM via llama.cpp as a
  deferred scanner writing `summary` findings for scanned documents).
  Deliberately plugins, never core: model weights, RAM, and licenses must
  stay opt-in.
