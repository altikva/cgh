# Proposal 007: embedding cgh in third-party code (the SDK surface)

Status: accepted 2026-07-31 (all recommendations; license regime 4,
the SDK-scoped MIT grant). Depends on proposal 001 (plugin types and loader);
reuses the bricks of 002 (egress gate), 003 (mode semantics), 006
(vision pipeline) and the secure-at-rest pseudonymization.

## Idea

cgh has two documented usage directions: extending it (plugins, via
`PluginAPI`) and consuming it as a tool (CLI, MCP server). A third
direction keeps coming up and is neither documented nor supported:
**embedding cgh's bricks inside someone else's program**. An agent, a
processing pipeline, an API, a batch job that wants, say, the vision
extraction combined with PII detection, as components of its own
process, without the cgh CLI, without an owner process, without MCP,
and without a `.codegraph/` repo underneath.

Technically this half-works today: the packages install, the modules
import, and the peer-dependency design means a scanner like `cgh_pii`
runs nearly standalone. But everything outside `plugin_api` is
internal and refactored without notice, several bricks assume a repo
root, and nothing tells an integrator what they may rely on. The
proposal: one documented, versioned SDK surface for embedding.

## Shape: `codegraph.sdk`

A single module in the core package, the only import path with a
stability contract:

```python
from codegraph import sdk

# Text scanning: run the installed scanners on content you provide.
findings = sdk.scan_text(text, path="invoice.txt", scanners=["pii", "secrets"])

# Egress decision: the proposal-002 gate as a pure function.
verdict = sdk.egress_decision(findings, mode="secure", allow_pii=False)
if verdict.allowed:
    call_cloud_model(text)

# Pseudonymization: the secure-at-rest primitive, caller-owned key.
safe = sdk.pseudonymize("pii.email", "joy@example.com", key=my_key)

# Vision (requires cgh-vision): inventory first, extract what fits.
inv = sdk.image_inventory("diagram.png")
if "architecture_diagram" in inv.content:
    extraction = sdk.extract_diagram("diagram.png", profile="default")
    markdown, mermaid = extraction.markdown, extraction.mermaid
```

Design rules, in tension order:

- **Explicit over ambient.** No `.codegraph/`, no config.toml, no
  global mode. Everything the pipeline needs arrives as arguments
  (config dicts, keys, profiles). The repo-flavored behavior stays in
  the CLI/MCP layer, which becomes one consumer of the SDK among
  others.
- **Stores are the caller's business.** `scan_text` returns findings,
  it does not persist them. An optional `InMemoryFindingStore` ships
  for pipelines that want dedup and querying without SQLite; the
  repo-backed store stays internal.
- **Capabilities discover themselves.** The SDK finds installed
  providers through the existing `cgh` entry-point group: `cgh-pii`
  provides the pii scanner, `cgh-vision` the image pipeline. A missing
  capability raises a clear error naming the package to install,
  never an ImportError from an internal path.
- **Secure by default.** Where a default exists, it is the safe one:
  `egress_decision` defaults to `mode="secure"`, pseudonymization
  output is the only PII representation the SDK returns unless the
  caller passes `raw=True`.

## Versioning contract

- `codegraph.sdk.SDK_API = 1`, bumped only on breaking changes, same
  discipline as `CGH_PLUGIN_API`.
- Everything importable from `codegraph.sdk` is covered by SemVer at
  the package level: breaking changes to the SDK mean a major (or,
  while 0.x, a minor with a deprecation window of one release and
  runtime warnings).
- Everything else in `codegraph.*` stays explicitly internal; the
  docs say so in one sentence at the top of the embedding page.

## Documentation

`docs/EMBEDDING.md`, recipe-first:

1. Scan text for PII inside an agent loop, decide egress, pseudonymize
   before logging.
2. Image pipeline in a batch job: inventory, route, emit markdown and
   Mermaid (the 006 pipeline as a library).
3. FastAPI middleware: refuse uploads whose findings carry block
   severity.
4. What the SDK does NOT give: the graph index, federation, shared
   memory, checkpoints; those remain repo-and-MCP features (v2 could
   revisit an `index_paths()` returning an in-memory graph, out of
   scope here).

## Licensing

Embedding is *using cgh*, so the dual MIT AND CC BY-NC-SA terms apply:
effectively non-commercial for third parties. The plugin exception
does not cover this direction (a plugin extends cgh; an embedder
ships cgh inside their product). Four regimes are possible:

1. Leave as is: third-party embedding stays non-commercial, ALTIKVA
   apps unaffected. Nothing to write.
2. Named commercial-embedding license: a LICENSE section stating that
   commercial embedding requires a separate agreement with ALTIKVA,
   with a contact. Makes the path visible without giving it away.
3. Extend the plugin exception to SDK consumers wholesale. Not
   recommended: one import would ship all of cgh into any commercial
   product, gutting the non-commercial intent.
4. **SDK-scoped MIT grant**: ALTIKVA, as sole rights holder, adds an
   additional permission to LICENSE: *code exercised solely through
   the `codegraph.sdk` surface may be used under the MIT license
   alone*. A facade cannot relicense what it calls, but the licensor
   can, and scoping the grant by surface keeps the boundary crisp:
   the commodity bricks (scan, gate, pseudonymize, vision) become
   freely embeddable, which drives adoption, while the product moat,
   the graph index, shared memory, federation, the MCP server, stays
   under the dual license because the SDK simply does not expose it.
   Two consequences to accept: the first-party scanner plugins the
   SDK reaches (cgh-pii, cgh-vision, cgh-classify) must move to plain
   MIT themselves or carry the same grant, or the promise is hollow;
   and MIT is irrevocable for every version shipped under it, so the
   SDK surface must be drawn knowing it cannot be narrowed later, only
   grown. If a real embedding market appears, the cleanest end state
   of this regime is a separate MIT `cgh-sdk` distribution that core
   depends on, Grafana-style: open bricks, licensed product.

Recommendation: regime 4 if the goal is adoption of the bricks
(matching the stated use case), regime 1 if the priority is keeping
every commercial conversation explicit. The SDK itself is
regime-neutral; the choice is the maintainer's.

## Open questions

1. **v1 surface**: scanners + gate + pseudonymization + vision
   (recommended), or also text summarization (`cgh-summarize`
   backends as `sdk.summarize(text, backend=...)`)? The backends are
   already close to pure functions, so the marginal cost is low.
2. **Location**: `codegraph.sdk` inside the core package
   (recommended: no new PyPI name, versions move together), or a
   separate `cgh-sdk` distribution that pins core?
3. **License regime** from the section above (recommendation: 4,
   the SDK-scoped MIT grant, if brick adoption is the goal; it
   requires deciding the plugin relicensing at the same time).
4. **Persistence contract**: in-memory store only (recommended for
   v1), or a documented `FindingStore` protocol third parties can
   implement over their own database?
5. **Vision dependency shape**: `sdk.image_*` raising until cgh-vision
   is installed (recommended, consistent with capability discovery),
   or a `cgh[vision]` extra that pulls it?
