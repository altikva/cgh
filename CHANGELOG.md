# Changelog

All notable changes to **cgh** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The Python import name is `codegraph`; the PyPI package and CLI are `cgh`.

## [Unreleased]

### Added
- **`cgh vision` caches its result per file**: vision inference is slow,
  so running the same image twice (once to look, again with `--out` to
  save) used to recompute the whole thing. The result is now cached by the
  input file's fingerprint plus the parameters that shape it (profile,
  models, hint, `num_ctx`), so a re-run returns instantly. A different
  profile never returns another profile's answer. Cached results live in a
  temp dir with a 24 h TTL (`[plugin.vision] cache_ttl_hours`, 0 disables;
  `cache_dir` to relocate); `cgh vision --force` recomputes and refreshes
  the cache. PDF pages are cached per page too. The key also covers the
  pre-scaling settings, since those change the pixels sent to the model.
- **`cgh examples`**: list runnable examples bundled inside the installed
  packages and install one locally to modify (`cgh examples install
  <name> [--dest DIR]`). Examples ship as package data, so this works
  with no git checkout and no network. Discovery spans the base package
  and every plugin (each can bundle its own under `<package>/examples/`);
  the base ships `starter-config` and cgh-vision ships `pdf-to-vision`.

### Fixed
- **cgh-vision sets a roomy Ollama context so images stop 400-ing**: a
  vision model encodes an image into many tokens, so a detailed diagram
  plus the prompt overflowed Ollama's small default context and returned
  `400 ... exceeds the available context size`. Each request now sets
  `num_ctx` (default 8192, `[plugin.vision] num_ctx` to change), and a
  context-overflow 400 points at that lever instead of a raw error. The
  manual-GGUF instructions and the `manual_gguf_steps` output now include
  `PARAMETER num_ctx 8192` in the Modelfile too.
- **cgh-vision: a request timeout no longer reads as a dead daemon**: when
  Ollama answered the socket but the extraction call ran past the deadline
  (the model loading on first use, or slow CPU inference), the error said
  "Ollama unreachable ... (is the daemon running?)", which is wrong and
  sent people chasing a daemon that was up. A timeout now says so and
  points at the fix (warm the model with `ollama run <model>`, raise
  `[plugin.vision] timeout_s`, or `--profile fast`); a real connection
  refusal still names the daemon. The per-call default timeout is raised
  to 300s (fast 120s) so a cold model on CPU has room. `timeout_s` was
  already configurable; only its default changed.

### Added
- **`cgh files`**: list the indexed files (optionally filtered by a path
  substring), and `cgh files --check <path>` answers "is this file
  indexed, and if not, why was it skipped" using the same decision the
  indexer makes (no parser for the suffix, over the `max_file_size_kb`
  cap, or an ignore rule). The why-skipped answer is a pure function of
  the file and config, so it works even while an owner holds the graph
  lock; listing falls back to the FTS in that case.
- **`cgh vision` reads PDFs** (cgh-vision): pass a `.pdf` and it rasterizes
  the pages to images (via pypdfium2, behind the `cgh-vision[pdf]` extra)
  and runs the vision pipeline per page, emitting a per-page report; a
  `--pages 1-3` / `--pages 2,5` selects pages. A non-image, non-pdf input
  now fails fast with a clear message instead of a cryptic Pillow error.
  pypdfium2 is PDFium (BSD/Apache, pip wheels, no system binary, not AGPL).
- **Plain-text files are indexed now**: `.txt`, `.text`, `.log`, `.csv`,
  `.tsv` and `.rst` had no parser, and the indexer skips any file no parser
  claims, so they were never indexed and never scanned for PII or secrets.
  A core plain-text parser now claims them and exposes their content as
  `scan_text`, with a preview section so they are findable.
- **Images are indexed now** (cgh-vision): the indexer skips any file no
  parser claims, so `.png` / `.jpg` / `.jpeg` / `.webp` were never indexed
  and the deferred vision scanner (which only runs on indexed files) never
  fired on repo images. cgh-vision now registers a minimal image parser, so
  an image becomes a known indexed file and the vision scanner reaches it.
  The scan stays deferred and gated as before (size bounds, a reachable
  vision backend, the egress rule for non-loopback endpoints); disable it
  with `[plugins] disabled = ["vision"]` if you do not want vision
  inference running over repo images.

### Fixed
- **PII scanning now reads a document's extracted text, not its raw
  bytes**: scanners were fed `read_text(errors="replace")` of the file,
  so for a pdf they matched the binary stream (phantom `pii.card` /
  `pii.phone` hits at huge offsets on diagram PDFs) and for a zip-based
  xlsx they saw compressed garbage and missed the real cell content
  entirely. Parsers now expose their extracted text as `FileIndex.scan_text`
  (pdf pages, xlsx cell values across data rows, docx paragraphs and table
  cells), and both the inline and the deferred scan paths run on that when
  present, falling back to raw bytes only for source files. This removes
  the diagram-PDF false positives and makes PII inside spreadsheets and
  Word tables detectable.
- **Tighter card and phone matching**: a card candidate must now start
  with a real network IIN (3/4/5/6) and not be a single repeated digit or
  a straight run, on top of the Luhn check; a phone candidate must hold a
  plausible E.164 digit count (8 to 15) and not be a mostly-separator
  spaced sequence. Cuts the coincidental matches that number-heavy text
  still produces even after the extracted-text fix.

### Added
- **Ollama backends auto-pick an installed model** (cgh-summarize and the
  cgh-pii LLM tier): both used to send a hardcoded model name
  (`qwen2.5:1.5b`, `qwen2.5:3b`), so a running daemon that had not pulled
  that exact model answered every file with `http error 404: not found`
  during `cgh init` / `reset`. They now query `/api/tags`: the configured
  model is used when installed, otherwise an installed generative model is
  auto-picked (family preference, embedding models excluded), and when
  nothing is installed the backend reads as unavailable so the scan
  degrades cleanly instead of erroring per file.
- **The default `config.toml` documents every plugin option**: the
  `[plugin.pii]` block now lists the LLM tier (`llm`, `llm_model`, the
  Ollama / OpenAI-compatible endpoints, `pii_llm_allow_remote`, `ner`,
  `disable_keys`) with defaults and one-line explanations, and the
  summarize / vision model options note the auto-pick behavior.

## [0.11.3] - 2026-08-12

### Added
- **cgh-pii gains an optional LLM detection tier**: with `[plugin.pii]
  llm = true` it probes each file with a local Ollama or a configured
  OpenAI-compatible endpoint and flags the PII the regex and NER tiers
  miss (names in odd formats, quasi-identifiers, addresses,
  context-bound identifiers). It runs deferred like NER (never inline),
  emits count-only `pii.llm.*` findings, and needs no extra package. On
  demand: `cgh pii probe <file>` lists what it would flag, and `cgh pii
  redact <file> --llm` folds its hits into the redaction (a catch-all
  `other` token for id numbers, orgs, credentials). Egress is gated the
  same way as `fetch_and_index`: a loopback endpoint is free, a
  non-loopback one needs `pii_llm_allow_remote = true`, and every probe,
  allowed or denied, is audited. A quote the model invents (absent
  verbatim) redacts nothing, so a hallucination cannot anonymize the
  wrong bytes.
- **cgh-vision prints the manual GGUF steps when every automatic route
  fails**: if a model is missing and the automatic Hugging Face pull
  cannot resolve it either, the error now spells out the by-hand path for
  that specific model (download the weights and the mmproj projector, write
  a two-line Modelfile, `ollama create` under the profile's name) instead
  of only pointing at the README. The same steps are documented under
  "When `ollama pull` is blocked" and aligned on the default `ggml-org`
  3B/4B repos.

### Fixed
- **The MCP proxy self-heals when its owner has died**: the owner shuts
  down when its last worker leaves, so a long-lived session's proxy could
  outlive the owner it attached to and then answer every tool call with
  `proxy: [Errno 61] Connection refused` forever, with no way back short
  of killing the stale proxies and restarting by hand. The proxy now
  treats a refused connection as a dead owner: it re-attaches to one that
  another session respawned, or spawns a fresh owner itself (no reindex,
  the graph is on disk), and retries the request against the new port.
  Recovery is bounded and fails cleanly with a proxy error if no owner can
  be brought up.

## [0.11.2] - 2026-08-08

### Added
- **cgh-vision auto-fetches a missing model from Hugging Face**: on the
  Ollama backend, a missing default model is now pulled from Hugging Face
  via Ollama's own `hf.co/...` pull (which works where the Ollama registry
  is blocked but HF is not), then aliased to the profile's name. It runs
  pre-flight (before the extraction bar) so Ollama's own download progress
  shows cleanly, and again as a retry if the model 404s mid-run. Opt out
  with `[plugin.vision] vision_auto_fetch = false`; an unmapped custom
  model still gets the manual guidance.

### Fixed
- **cgh-vision: a clear error instead of a crash when Ollama is missing
  the model or is down**: `_ask_ollama` did not catch HTTP / connection
  errors, so a 404 (model not pulled) or an unreachable daemon raised a
  raw urllib traceback and a crash report. It now raises a VisionError
  the CLI catches: when auto-fetch cannot help, a 404 names the model and
  the ways to get it (ollama pull, a local GGUF, or `cgh vision setup
  --llamacpp`), an unreachable daemon says so, and the command exits
  non-zero cleanly.

## [0.11.1] - 2026-08-06

### Fixed
- **MCP server no longer times out on startup for large repos**: the
  owner ran the startup `--reindex` synchronously before publishing its
  port, so a big repo's index (30s+) blocked the MCP initialize handshake
  and the client gave up ("failed to restart cgh mcp, timeout after
  30000ms"). The reindex now runs in a background thread; the owner
  publishes its port and answers the handshake immediately, and queries
  hit the existing graph until the reindex catches up.
- **Secure-mode init crashed on a non-UTF-8 config.toml**: enabling
  secure mode reads `.codegraph/config.toml` to edit the `mode` line; a
  CP1252 byte in it (an em dash in a comment) raised UnicodeDecodeError
  and took down every federated child refresh. It now decodes leniently
  and the write-back repairs the file to valid UTF-8. A failed federated
  child now also prints its full traceback (they run captured), so such
  crashes are diagnosable from the parent run.
- **Incremental reindex shows progress**: it drove no progress callbacks,
  so `cgh init` with the incremental choice was a silent wait. It now
  feeds the same spinner/bar as a full scan (and its fallback does too).
- **The `cgh` banner is the correct figlet again**: the hand-tweaked
  logo had a misaligned underscore floating above the `h`; it is now the
  clean Standard figlet of `codegraph`.
- **The non-UTF-8 tolerance now also covers the agent docs read during
  init**: `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` are read to splice in
  the codegraph block; a CP1252 byte in a templated one crashed init
  (notably every federated child). Those reads decode leniently too.
- **`cgh init` no longer crashes on a non-UTF-8 ignore file**: a
  template `.gitignore` carrying a CP1252 byte (an em dash in a header
  comment) raised `UnicodeDecodeError` and took down init across every
  federated subrepo. The `.gitignore`, `.cghignore` and `.bobignore`
  reads now decode leniently (`errors="replace"`); they only scan for a
  substring, so a mangled byte elsewhere is harmless.
- **`cgh init` self-heals a corrupt DuckDB graph**: when indexing hits
  the DuckDB `Failed to delete all rows from index` fatal (a corrupt ART
  index left by an earlier crash), index_repo now wipes the graph and
  retries a full scan once instead of crashing. The graph is derived
  from source, so only the index is rebuilt (FTS, knowledge, config
  stay).
- **`cgh reset` now removes the DuckDB graph**: the name filter only
  matched the Kuzu `graph.db`, so on a DuckDB repo (the default since
  v0.4) reset left `graph.duckdb` in place and could not recover a
  corrupt graph. It now targets `graph.*` (both backends and their
  wal/shm sidecars), keeping `call_log.db` (knowledge) untouched.
- **Re-indexing no longer corrupts the trigram FTS index**: the symbol,
  memory and plan upserts did `INSERT OR REPLACE` into the content table
  (which reassigns the row's rowid) and then wrote the new postings
  without removing the old rowid's, so every reindex orphaned postings
  in the external-content trigram index until it corrupted into
  `database disk image is malformed` and crashed `cgh init`. The upserts
  now delete the old rowid's postings before the replace, and
  `delete_file_symbols` rebuilds the index from its content table and
  retries once when it meets an already-corrupt index instead of
  crashing the reindex. Indexes corrupted by an earlier build self-heal
  on the next index.


### Added
- **Progress feedback across every init phase**: spinners while detecting
  AI tools, searching for subrepos and counting files, plus the indexing
  and federated-refresh bars. Rendered on real terminals AND on git-bash /
  mintty (where isatty is unreliable), and suppressed via CGH_NO_PROGRESS
  in background / captured runs (federated children, hooks) so a pipe or
  log never gets ANSI. A federated child that fails now shows its full
  traceback (they run captured), not just the last line.
- **Progress bar while refreshing federated subrepos**: `cgh init` runs
  a full sub-init per child, so the refresh of many subrepos no longer
  waits silently. A live bar shows the current child, count and elapsed
  time, then the per-child results are printed.

## [0.11.0] - 2026-08-05

### Added
- **`find_callees` walks the call chain in one call**: it gained a
  `max_depth` argument (default 1, unchanged behavior). With `max_depth>1`
  it traverses the CALLS edges forward and returns the ordered chain, each
  callee tagged with its `depth`, so tracing a flow no longer costs one
  round-trip per hop. Bounded by a depth ceiling and fan-out / total caps
  (`truncated` flag when hit), and federated per scope like the one-hop
  form. The `cgh-usage` guidance now steers multi-hop questions to a
  single server-side traversal (`find_callees(max_depth)`, `impact_of`,
  `path_between`, `subgraph`) instead of chaining single-hop lookups.
- **Intent-driven filtering on `impact_of`**: a large blast radius used
  to be capped to the arbitrary first N impacted nodes. It now takes an
  optional `focus`, and when the result overflows the cap it keeps the
  nodes matching the focus terms (matched on path, role and layer)
  instead, so a relevant file beyond the cap in raw order survives
  (`impact_of("db.py", focus="router")`). The truncation is never
  silent: the response carries a `focus_note` saying how many were kept
  and dropped, and how to narrow. The `focus_filter` helper is shared
  and applies to the other large-output tools as they adopt it.
- **Fetch a URL into the searchable index**: `fetch_and_index` (MCP)
  and `cgh fetch <url>` pull a page, reduce it to text, chunk and
  index it, so `search_fetched` / `cgh fetch --search` read it back
  with no further network. Results cache by URL with a TTL (default
  24 h). A fetch is gated network egress: http/https only, private,
  loopback and link-local hosts refused (SSRF), refused in secure
  mode unless `[codegraph] allow_fetch` is set, and every fetch and
  refusal is written to the activity log.
- **PII redaction: `cgh pii redact` and `sdk.redact_text`**: produce an
  anonymized copy of a text or markdown file, keeping only the
  categories you ask for (`--only person` to anonymize just names).
  Tokens are either numbered placeholders (`[PERSON_1]`, distinct
  within the document) or keyed pseudonyms (`<pii.person:hex>`, stable
  across documents with a shared `CGH_REDACT_SECRET`); the same value
  always maps to the same token. Person and location names come from
  the NER tier (`cgh-pii[ner]`); requesting them without it fails
  clearly. Since NER can miss repeat mentions, a detected name is
  propagated to all its literal occurrences, so it is redacted
  everywhere. Word documents are redacted with the `docx` extra
  (`pip install "cgh-pii[docx]"`): body paragraphs and table cells,
  one shared token map across the file, formatting inside changed
  paragraphs flattened (the only way to redact PII split across
  runs). PDF stays unsupported (real redaction needs an AGPL
  library); extract the pdf text and redact that.
- **`cgh vision --hint`** (and the `hint` config key): a short steering
  instruction appended to the extraction prompts, for example
  `--hint "labels are in French"` or "prefer application service
  names". It is added after the format rules and never replaces the
  JSON contract, so it nudges the model without breaking parsing.
- **`cgh vision setup --llamacpp`**: one command to run vision without
  Ollama. It finds or installs llama.cpp through its official channel
  (brew on macOS, the signed GitHub-release binaries on Windows, never
  a bundled binary), writes a `[plugin.vision]` block pointing
  cgh-vision at a local llama-server, and offers to start it. The
  server auto-downloads our default vision model and its mmproj
  projector on first run, and stays the user's process to keep running,
  like the Ollama daemon; cgh starts it on request but does not
  supervise it. Benchmarked as the best node/edge transport.
- **cgh-vision runs without Ollama, on any OpenAI-compatible vision
  endpoint**: set `openai_base_url` and the transport switches to
  `/chat/completions` with a base64 image. That serves the GGUF
  weights from Hugging Face through llama.cpp's own `llama-server`
  (no daemon, no `ollama.exe`), or LM Studio, vLLM, or an approved
  internal gateway. Egress is judged from the active endpoint, not the
  backend name: a loopback llama-server stays local and secure mode is
  satisfied, a remote gateway is gated like any cloud. Ollama stays the
  default; nothing changes without the new key.
- **`cgh vision` helps install Ollama through its official channel**:
  when the daemon is unreachable it now prints the OS-appropriate
  install command (winget on Windows, Homebrew on macOS, the vendor
  script shown but never auto-piped on Linux) and, in an interactive
  terminal, offers to run it. cgh points at the publisher's own
  installer only: it never bundles, mirrors or obfuscates the binary.
  A network that blocks every official channel is a policy to resolve
  with IT or by pointing `ollama_url` at an approved internal server,
  not something cgh works around.
- **cgh-vision names the models it is missing, and documents the route
  when `ollama pull` is blocked**: `cgh vision` now checks the daemon's
  model list before starting and prints what is absent with the pull
  command, instead of failing a minute later mid-extraction. Corporate
  networks that block the Ollama registry have a documented
  alternative in the plugin README: download the GGUF weights and the
  mandatory vision projector from Hugging Face, register them with
  `ollama create`, and point `nodes_model` at the local name. cgh only
  ever asks the daemon for a name, so a locally registered model is
  indistinguishable from a pulled one.
- **cgh-vision consults a second structure reader when the first comes
  back empty-handed**: an extraction with two boxes or fewer, or with
  no arrows at all, now gets one retry from the arrow model (config
  `fallback_model`, empty to disable, off on the `fast` profile), and
  the retry replaces the first result only when it found more. The
  two models fail differently, which is the whole point: benchmarked
  over every local vision model, the arrow reader rescues all five
  thin-line cases the primary reader cannot see (2 nodes / 1 edge
  becoming 13 / 27), including the two that pre-scaling could not
  save, while needing no extra download. It never fires on the
  synthetic corpus the default already reads correctly, so precision
  and zones are untouched.

### Changed
- **Symbol search fuses BM25 with a trigram substring ranking (RRF)**:
  `search_symbols` / `fts_search` kept a word-tokenized FTS index that
  splits identifiers ("DonationHandler" to "Donation Handler"), so a
  fragment inside an identifier ("andl") matched nothing. A parallel
  trigram index now catches those fragments, and the two rankings are
  merged with Reciprocal Rank Fusion, so whole-word queries still win
  and partial-identifier queries finally hit. Existing indexes
  backfill the trigram table once on open; no reindex needed. Fixed
  along the way: external-content FTS deletes replayed empty strings
  instead of the indexed values, which left the index inconsistent.
- **`memory_search` and `plan_search` now fuse the same way**: both
  gained a parallel trigram index and RRF fusion, so a fragment inside
  a memory title or a plan slug ("ationpars" inside "DonationParser")
  matches where the word-tokenized index missed it. Existing indexes
  backfill their trigram table once on open, and the deletes pass the
  real indexed values so the external-content index stays consistent.

### Fixed
- **The installers survive a bad network and speak to internal
  mirrors**: `install.sh` aborted on the first failing installer
  instead of falling through to the next (`set -e` on the uv line),
  and `install.ps1` was worse, reporting success after a failed
  install because a non-zero native exit does not raise in PowerShell.
  Both now try uv, pipx and pip in turn on their exit status, and the
  PowerShell version also catches the terminating error that
  PowerShell 7.4 raises instead. New knobs, honored by all three
  installers: `CGH_INDEX_URL` for an internal PyPI mirror,
  `CGH_TRUSTED_HOST` for its self-signed certificate, `CGH_TIMEOUT`
  and `CGH_RETRIES` for a slow link. When everything fails the message
  names those options rather than leaving a stack trace.
- **Windows stops flashing a console window on every agent action**:
  the Claude Code hooks fire on each tool call and `cgh.exe` is a
  console application, so Windows created (and destroyed) a console
  for each one, multiplied by every repo in a federated workspace.
  cgh now also ships `cghw`, the same entry point built as a
  windowless launcher, and the hook wiring points at it on Windows
  when it is present, falling back to the console launcher on older
  installs. Three plugin subprocess calls that also lacked the
  no-window flag are fixed alongside: the summarize agent-CLI backend
  (one spawn per summarized file, inside the detached owner) and the
  `git ls-files` calls of classify and summarize.

### Security
- **SSRF hardening on `fetch_and_index`** (found by the commit security
  review): the guard only checked bare IP literals, so three bypasses
  slipped through: a hostname resolving to a private address
  (DNS-based SSRF), a decimal/hex/IPv6-encoded loopback
  (`http://2130706433/`), and a public URL redirecting to a private
  host. The guard now resolves the host through getaddrinfo and refuses
  if any resulting IP is private, loopback, link-local, reserved,
  multicast or unspecified (a host that resolves to nothing is refused
  too, fail closed), and a custom opener re-guards every redirect hop.

## [0.10.1] - 2026-08-03

### Fixed
- **Scanners crashed on repos indexed from outside their directory**:
  the plugin registry loads once per process and the CLI loads it
  before `--root` is parsed, so running `cgh index --root <repo>`
  from elsewhere left every scanner bound to `repo_root=None` and the
  first `Path(None)` blew up per file (`argument should be a str or
  an os.PathLike object`, seen on Windows/OneDrive). The scan sites
  now late-bind the authoritative root, and the classify and
  summarize scanners answer empty instead of crashing when loaded
  rootless (SDK `scan_text`). The xlsx parser also silences
  openpyxl's cosmetic "no default style" warning that spammed stderr
  on such repos.

## [0.10.0] - 2026-08-03

### Added
- **A shared `--out PATH` option for artifact-emitting verbs**: the
  result still prints on stdout (pipeable), `--out` also writes it to
  a file with a stderr confirmation, and interactive sessions without
  it get a one-line tip advertising the flag. Wired on `cgh vision`
  and `cgh impact`; plugins reach the same contract through the
  plugin API (`add_out_option`, `emit_result`).
- **A shared `--format md|json` option**: same convention as `--out`
  (one helper, plugin API re-export). `cgh vision --format json`
  emits the structured extraction exactly as the SDK returns it
  (inventory, diagram nodes/edges/zones/identities, mermaid, tables,
  charts) instead of the markdown projection, and composes with
  `--out report.json`. `cgh impact --format` and `cgh findings
  --json` already spoke JSON and are unchanged.
- **`cgh vision` shows its progress**: the pipeline announces each
  model pass (inventory, structure, enrichment, arrows, tables) to an
  optional observer and the CLI renders it as a transient spinner
  with elapsed time on stderr, so the 30 seconds of model time no
  longer look like a hang. The SDK surface stays silent by default.

### Changed
- **cgh-vision pre-scales small images before extraction**: images
  whose smaller dimension is under 1000 px are upscaled 2x (Lanczos)
  for the diagram passes, which the benchmark showed rescues
  thin-line drawio exports (2 nodes / 1 edge becoming 6 / 7) and
  never hurts the others; 3x was measured worse than 2x. Tunable via
  `prescale` / `prescale_min_px` under `[plugin.vision]` (documented
  in the default config template), and the plugin now depends on
  pillow.

### Fixed
- **Zones emitted as nested lists render correctly**: some models
  return `zones: [["Cluster GKE"], []]`; the labels came out as
  stringified Python lists in the markdown and the Mermaid subgraphs,
  and empty zones survived. Nested lists now flatten to their label
  and empty zones are dropped.

## [0.9.0] - 2026-08-02

### Added
- **Public exception hierarchy** (`codegraph.errors`): `CodegraphError`
  base with `ConfigurationError`, `BackendError`, `IndexingError`;
  `CapabilityMissing` now inherits it (RuntimeError kept for
  compatibility) and the SDK exports the base, so embedders catch one
  type for everything cgh raises on purpose.
- **PEP 561 markers everywhere**: `py.typed` ships in the core package
  and all six plugins; SDK consumers finally get type checking from
  the annotations that were already there.
- **Strict pytest configuration**: `--strict-config --strict-markers`,
  declared `kuzu`/`network` markers and testpaths in pyproject; a
  typo'd marker is now an error instead of a silently empty filter.
- **cgh-vision plugin** (in `plugins/cgh-vision`, published separately):
  the benchmarked image pipeline as a deferred scanner and a `cgh
  vision` CLI verb. A content inventory decides what each image
  contains (never assuming a diagram), then only the warranted
  extractors run: diagrams to markdown + Mermaid (qwen nodes, gemma
  arrows constrained to the found labels), tables and charts to data,
  dense text to a summary. Identities read off diagrams (IPs, FQDNs,
  hostnames) are split out of labels and recorded as
  `pii.image_identity` findings, so the secure-at-rest layer
  pseudonymizes them. Local Ollama only; the SDK `image_*` functions
  now resolve. Joins the `plugins` and `full` extras.
- **Embedding SDK** (`codegraph.sdk`): the documented surface for
  using cgh's bricks inside third-party code, without CLI, owner, MCP
  or a `.codegraph/` repo: `scan_text` over installed scanners, the
  egress gate as a pure function (secure allowlist by default),
  caller-keyed pseudonymization, `summarize` through the cgh-summarize
  backends (local-only by default), vision entry points raising a
  clear error until cgh-vision ships, and an in-memory finding store.
  `SDK_API = 1`, SemVer on the surface, everything else stays
  internal. Recipes in docs/EMBEDDING.md.
- **SDK embedding exception** (LICENSE): code exercised solely through
  `codegraph.sdk` may be used under MIT alone, including commercially;
  the graph index, MCP server, federation and shared memory stay under
  the dual license. cgh-pii, cgh-classify and cgh-summarize move to
  plain MIT so the grant is real end to end.
- **Sensitive findings pseudonymized at rest (secure mode)**: `pii.*`
  and `secret.*` finding values are replaced at write time by stable
  one-way pseudonyms keyed per repo (HMAC, `.codegraph/pseudo.key`),
  so the raw datum never reaches disk and reading the SQLite files
  directly, bypassing MCP, yields nothing recoverable. Dedup and
  cross-file search keep working on the pseudonyms.
- **The index is guard-protected in secure mode**: agent Read/Grep and
  any shell command touching `.codegraph/` are denied with a reason
  pointing at the MCP tools, and the static deny lists (Claude
  settings, `.bobignore`) carry a standing index entry.

### Changed
- **Claude usage guidelines install as a native rule**: when the
  installed Claude Code supports the `.claude/rules/` directory
  (probed via `claude --version`), `cgh init`/`cgh setup claude` write
  `.claude/rules/cgh-usage.md`, a file cgh owns outright
  (auto-discovered, versioned with the repo, overwritten on update),
  and migrate away the legacy marker block from CLAUDE.md so the
  guidance is not paid twice. Older or undetectable Claude versions
  keep the legacy CLAUDE.md block unchanged. The design proposals, the
  benchmark harness and the maintainer release runbook also leave the
  public tree for a gitignored internal/ directory.
- **Parser dataclasses use `slots=True`**: the eight `FileIndex`
  building blocks (`SymbolDef`, `ClassDef`, `ImportRef`, ...) are the
  highest-volume allocations during indexing; slots cut their peak
  memory by a measured 13% on 100k instances with no API change. (An
  FTS write-batching change was prototyped alongside, benchmarked at
  exactly zero gain, and dropped.)
- **The three longest functions are split into their jobs**: the CLI's
  500-line `main()` becomes four grouped command registrars plus a
  module-level parser class; `cmd_init` extracts the AI-tool setup and
  the federation offer into named steps; `index_repo` separates
  discovery, filtering, deletion handling and extra_dirs indexing from
  the shared index loop. Same behavior, tested; each piece now reads
  and diffs on its own.
- **Background processes use real logging**: the owner, the MCP proxy,
  the watcher and the deferred-scan worker emit through per-module
  loggers behind one `[codegraph]`-prefixed stderr handler (configured
  at the two daemon entrypoints, never on the root logger, so an
  embedding application keeps control). Twenty stderr prints converted
  with levels; owner.log now carries levels and module names, and
  unconfigured library use still surfaces warnings through logging's
  last-resort handler. CLI user output stays print/rich.
- **CI proves the floors and the artifact**: a new advisory job
  resolves every direct dependency to its declared minimum
  (`--resolution lowest-direct`) and runs the suite, so a lower bound
  nobody tests can no longer pretend to be a compatibility statement;
  every PR now also builds the wheel and passes `twine check` instead
  of discovering packaging breakage at release time; and the dev
  toolchain is declared in `[dependency-groups]`.
- **Explicit ruff configuration, whole-tree formatting, format gate in
  CI**: the linter ran on its narrow defaults (E4/E7/E9/F), leaving
  bugbear, security, simplify and modernize off. `[tool.ruff.lint]`
  now selects E/F/B/I/S/SIM/UP/RUF with documented, deliberate
  ignores (formatter owns line length; audited subprocess posture;
  triaged except-pass discipline) and per-file test allowances; ~180
  findings were auto-fixed or corrected (including a real
  loop-variable closure bug in the bench report), the long-deferred
  `ruff format` debt is settled (40 files), and CI now gates
  formatting alongside linting.
- **The plugin API covers what plugins actually need**: the finding
  store, activity log, knowledge record, config resolution, parser
  lookup, federation children, subprocess hygiene and a `server_root()`
  accessor are re-exported (lazily) from `codegraph.plugin_api`, the
  one import path with a stability promise. All six first-party
  plugins migrated off `codegraph.state/*` and friends, and a boundary
  test now fails any plugin import that reaches into internals, which
  is what keeps `API_VERSION` honest.
- **Plugin floors in the install extras track plugin releases**: a
  stale floor let `uv tool install --force` keep an already-resolved
  older plugin, so the extras now pin the floors to what this release
  ships. The five published plugins get patch releases carrying the
  MIT relicense and the audit fixes (`cgh-docs`, `cgh-pii`,
  `cgh-classify`, `cgh-bugreport` 0.1.1; `cgh-summarize` 0.2.1) and
  `cgh[plugins]`/`cgh[full]` require them; cgh-vision 0.1.0 publishes
  for the first time.

### Fixed
- **One backend factory, identifier allow-list in both backends**:
  which graph backend a repo uses (and which file) was decided by
  copies of the same if/else in the connection cache, federation and
  the status commands; `detect_backend_file` in `core.db` is now the
  single tie-break authority and `open_graphdb_file_ro` the shared
  read-only opener (federation and status consume them; a
  factory-opened Kuzu connection closes its Database handle with the
  connection). And every SQL/Cypher identifier interpolation in the
  two adapters (26 sites: where/contains field names, return_fields,
  order_by, edge property keys) now passes an allow-list gate that
  raises `BackendError` on anything not identifier-shaped, closing
  the latent injection the audit flagged even though current callers
  pass constants.
- **The plugin test suites actually run**: pytest only collected
  `tests/`, so the six plugins' own suites (~95 tests) never executed,
  locally or in CI. They are collected now (self-skipping when a
  plugin is not installed), the lowest-resolution advisory job
  installs the plugins so their dependency floors (pypdf, python-docx,
  openpyxl) are proven too, and the plugins gained module loggers: a
  corrupt pdf/docx/xlsx that indexes empty now leaves a warning
  instead of looking like success, and raised errors use named
  exception types (`SummarizeError`, `VisionError`).
- **The declared dependency floors are now real** (first catches of the
  lowest-resolution CI job): the `duckdb` floor rises to 1.2 because
  1.0 and 1.1 reject the node upsert (their binder refuses
  `ON CONFLICT DO UPDATE` touching a constrained column); `pyyaml` is
  declared as the direct dependency it always was (the YAML section
  parser imported it, but only a transitive install made it appear);
  and the init wizard's instruction style uses a literal gray instead
  of the `dim` attribute that only the newest prompt_toolkit parses,
  which made `cgh init` crash outright on older resolutions. The full
  suite now passes with every direct dependency at its floor.
- **Connection caches keyed by repo root**: the graph connection cache
  and the knowledge/call-log connection were first-caller-wins process
  globals, so a second repo touched in the same process (federation,
  SDK embedding, tests) silently received the first repo's database.
  Both now follow the findings-store pattern: a dict keyed by resolved
  root, per-root or global reset, and call_log gains the
  reset_for_tests hook it lacked.
- **`cgh graph` works on DuckDB**: the CLI visualization went through
  raw-Cypher generators that only Kuzu understood, so every scope
  silently rendered "No edges found" on the default backend. The
  generators now live once in `codegraph.viz.graphviews`, speak the
  GraphDB protocol only, and serve both entry points (the
  `visualize_graph` MCP tool and the CLI); the Kuzu-only ancestors are
  retired, and the init wizard's file count uses the protocol too.
- **Silent failures on write paths now log**: a failed node deletion in
  the incremental reindex (previously a ghost-node source reporting
  success), dropped CALLS edges from the precise resolver, a malformed
  config skipping `extra_dirs`, a failed read-only close before a
  write open, and the FTS MATCH falling back to LIKE all leave a trace
  in the activity log or on stderr instead of being swallowed.
- **Timeouts on every git subprocess**: seven call sites (post-commit
  hook, git hooks setup, changed-files MCP tool, `cgh changes`) ran
  git with no deadline; a wedged git froze the caller. All now time
  out (30-60 s) and their callers handle the expiry.

### Security
- **A "local" backend claim is now earned by the URL, not declared**:
  the Ollama backends of cgh-summarize and cgh-vision labeled
  themselves local while `ollama_url` accepted any host, so in secure
  mode file content and raw image bytes could reach a non-loopback
  daemon without ever meeting the egress gate. A shared
  `is_loopback_url` helper (exposed through the plugin API) now
  classifies the backend from the configured host: summarize treats a
  remote Ollama as cloud (gated), vision refuses it outright in secure
  mode and audit-logs the departure in assist mode. Malformed URLs
  read as unavailable instead of crashing the probe.
- **cgh-bugreport's mode probe fails closed**: an unreadable config
  made the pre-send secure-mode confirmation silently skip; unknown
  mode now behaves as secure. The report spool directory is also
  created 0700, and the plugins' `git ls-files` calls carry the
  timeout every other subprocess in the tree already had.
- **The secure-mode probe fails closed**: if `record_findings` cannot
  determine the guard mode (unreadable config, transient error), it
  now pseudonymizes sensitive values instead of silently falling back
  to raw storage, and logs the failed probe. A broken probe can no
  longer void the secure-at-rest guarantee.
- **`add_directory` is confined**: the MCP tool indexes paths inside
  the repo root freely, but a path outside the root is only accepted
  when a human already declared it in `[codegraph] extra_dirs` (via
  `cgh add-dir add` or config.toml), and the acceptance is logged.
  Without this, any prompt-injected MCP client could walk and index
  arbitrary readable directories, making their content queryable.

## [0.8.0] - 2026-07-29

### Added
- **Secure mode from the init wizard**: `cgh init` now asks whether to
  enable secure mode (assist stays the default and `--yes` alone never
  changes posture), and `cgh init --secure` enables it without
  prompting. Federated children initialized by a secure parent inherit
  the posture automatically, so a monorepo hardens as one unit.
- **Self-documenting default config**: the `config.toml` written by
  `cgh init` now lists every option cgh reads, active defaults live,
  optional ones commented out with an explanation (`mode`, log
  rotation, `extra_dirs`, `[plugins]` narrowing and the first-party
  `[plugin.*]` tables included). The file doubles as the reference a
  user edits instead of hunting through the docs, and a test keeps
  the template valid TOML even with every option uncommented.
- **IBM Bob integration** (`cgh setup bob`, detected by `cgh init`): a
  repo using Bob is recognized by its `.bob/` folder, a `.bobignore`
  file, or the `bob` binary on PATH. Setup registers the MCP server in
  `.bob/mcp.json` (Bob's project-level config), installs the bundled
  skills verbatim under `.bob/skills/` (Bob speaks the same Agent
  Skills standard as Claude Code, SKILL.md front matter included),
  drops the usage guidelines in `.bob/rules/` where every Bob mode
  loads them, and in secure mode the guard mirrors barred paths into a
  managed `.bobignore` block that `cgh guard sync` keeps fresh. Bob
  publishes no pre-tool veto hook, so the enforcement level is
  declared "partial": static file denies, honestly labeled.
- **IBM Bob summarize backend** (`cli:bob` in cgh-summarize): BobShell's
  headless mode (`bob -p`) joins the agent CLI backends. No model
  option: Bob's orchestration engine routes each call to a model on
  its own. Auto-selected after claude/gemini/codex when installed,
  and the Windows `.cmd` shim handling applies to it like the others.

## [0.7.3] - 2026-07-29

### Fixed
- **Deferred scans of binary documents on Windows**: docx/xlsx files
  decoded with `errors="replace"` kept embedded null characters, which
  no OS accepts in a subprocess argument, so summarize calls died with
  "embedded null character". Nulls are now stripped at both scanner
  text entry points, and when the excerpt is replacement-character
  soup the summarize prompt falls back to the parser's section
  previews (the real document text) instead of raw bytes.
- **npm-installed agent CLIs on Windows**: `claude`/`gemini` installed
  through npm are `.cmd` shims that `shutil.which` finds but
  `CreateProcess` cannot launch, so every summarize attempt failed
  with "[WinError 2] file not found" misleadingly pointing at the
  scanned file. The summarize backends now run the resolved
  `.cmd`/`.bat` path through `cmd /c`, and a backend failure is
  re-raised named after the backend, leaving nothing recorded so the
  file retries on its next change.

## [0.7.2] - 2026-07-29

### Added
- **`cgh[full]` extra**: the five first-party plugins plus the `langs`
  and `lsp` extras in one install. The tree-sitter grammars ship abi3
  wheels covering every Python cgh supports and jedi is pure Python,
  so the superset is safe everywhere; only the legacy kuzu backend
  stays out. The install scripts' `CGH_PLUGINS=1` now uses it.

## [0.7.1] - 2026-07-29

### Added
- **One-shot install**: `pip install "cgh[plugins]"` (or
  `uv tool install "cgh[plugins]"`) brings the core and the five
  first-party plugins together, and the one-line install scripts do
  the same with `CGH_PLUGINS=1` (bash) or `$env:CGH_PLUGINS = 1`
  (PowerShell).

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
  the plugin architecture design.

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

[Unreleased]: https://github.com/altikva/cgh/compare/v0.11.2...HEAD
[0.11.2]: https://github.com/altikva/cgh/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/altikva/cgh/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/altikva/cgh/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/altikva/cgh/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/altikva/cgh/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/altikva/cgh/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/altikva/cgh/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/altikva/cgh/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/altikva/cgh/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/altikva/cgh/compare/v0.7.0...v0.7.1
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
