# Proposal 002: file summaries with an egress gate

Status: decisions recorded 2026-07-29, awaiting sign-off on the corpus
insights section. Depends on proposal 001 (plugin loader, scanner
surface, Finding store) and pairs with cgh-classify.

## Idea

For every indexed file that is NOT confidential, cgh can produce a prose
summary by delegating to an AI CLI already installed on the machine
(Claude Code, Gemini CLI, Codex CLI) running a light model, or to an
embedded local model. Summaries are stored as findings, searchable
through the FTS, and served to agents so they can orient in one call
instead of reading files.

The point is not the summary itself, it is the rule that produces it:
**confidentiality classification acts as an egress policy**. A file
flagged confidential never leaves the machine; cloud-backed summarization
is only allowed for files the gate clears. This turns the classifier from
a passive label into an active guardrail, which is the direction the
whole plugin effort points at.

## Shape: one plugin, several backends

`cgh-summarize` (first-party, per proposal 001 naming) with a backend
per summarizer:

| Backend | What it runs | Egress | Notes |
|---|---|---|---|
| `cli:claude` | `claude -p` headless, light model (Haiku tier) | cloud | uses the CLI's own auth and billing |
| `cli:gemini` | `gemini` headless, flash tier | cloud | same |
| `cli:codex` | `codex exec` | cloud | same |
| `local` | small quantized LLM via llama.cpp (SmolLM2 / Qwen 1.5B class) | none | heavier install, weakest quality, zero egress |
| `structural` | cgh's own `file_summary`, no model at all | none | already exists, free, default for code |

Backend detection reuses the AI-tool detection `cgh init` already does.
Nothing is enabled silently: during `cgh init` (or `cgh summarize enable`)
cgh lists the detected CLIs and asks. Consent is per repo, stored in
`[plugin.summarize]` in the machine-local config.

## The egress gate

Two user profiles must both stay first-class: the user who runs cgh to
cut token spend and wants summaries with minimum ceremony, and the user
whose priority is controlling what leaves the machine. One knob covers
both, `egress` in `[plugin.summarize]`:

- `egress = "open"` (default): the gate blocks on what the Finding store
  actually knows. A `confidential` finding: blocked, always. Any
  block-severity finding (e.g. `secret.aws_key`): blocked. `pii.*`
  findings: blocked by default, `allow_pii = true` to override. A file
  with no findings at all goes through.
- `egress = "strict"`: allowlist mode. Only files explicitly labeled
  non-confidential (by cgh-classify or by hand) ever reach a cloud
  backend. Without labels, nothing goes out.

## Size threshold

Short files are not worth a model call, and their structural summary is
already the whole story. Summarization triggers above a configurable
threshold, `min_kb = 4` by default (roughly a page of text). Below it,
the `structural` backend still answers for free. The threshold applies
per file after parsing, so a 200-page PDF whose text extraction is tiny
still gets skipped honestly.

The `local` and `structural` backends bypass the gate: nothing leaves the
machine, so they may summarize anything, including confidential files
(their summaries inherit a `confidential` finding when the source has
one, so the summary is gated the same way the file is).

Every cloud summarization writes an audit line (file, backend, model,
blob SHA, timestamp) to `.codegraph/activity.log`, so "what left this
machine" has one answer.

## What gets sent, and what for

Not the raw file. The prompt scaffold is cgh's structural `file_summary`
output (symbols, docstrings, roles, sections) plus a capped excerpt of
the content. Cheaper, and it anchors the model on structure.

Per file type:

- **Code**: `structural` is the default and usually enough; an LLM pass
  is opt-in per role (e.g. summarize entrypoints only).
- **Documents** (pdf, docx, xlsx via cgh-docs) and long markdown: this is
  where LLM summaries earn their keep. Default backend order: the
  configured CLI, else `local` if installed, else skip.

## Mechanics

- Runs as a deferred scanner (proposal 001 queue): never in the watcher
  hot path, cached by blob SHA, rate-limited, resumable.
- Output: a `summary` finding (file-level) plus optional `summary.<lang>`
  variants. Findings feed the FTS per decision 2 of proposal 001, so
  searching a phrase can hit a summary and lead to the file.
- Served via the existing query tools and a `summaries(path?)` MCP tool;
  `context_for_task` can weave summaries in when present.
- Cost control: light models only by default, a per-run file cap, and a
  `cgh summarize status` showing queue depth and how many files were
  sent where.

## Corpus insights (the second product of summaries)

Per-file summaries are the substrate; the payoff is what a capable model
sees when it reads them all at once. `cgh insights` (CLI) and a
`corpus_insights` MCP tool batch the gate-cleared summaries, together
with signals cgh already computes for free (graph stats, layer diagram,
hotspots, import cycles), into one prompt to the configured CLI backend
and ask for what no single-file view shows: hidden patterns, duplicated
concepts across modules, architectural drift, coupling that contradicts
the declared layering.

Rules:

- The gate applies at corpus level too: only summaries of files that
  cleared the egress gate are included, and summaries carrying an
  inherited `confidential` finding are excluded from cloud-bound
  batches.
- This is also the token-saving story at its best: one call over a few
  hundred short summaries costs a fraction of reading the corpus, which
  is exactly the tradeoff cgh exists for.
- Results are written to the knowledge store (`knowledge_record`,
  kind `pattern` or `note`, tagged `insights`), so agents recall them in
  later sessions instead of re-deriving them.

## Decisions (2026-07-29)

1. **Both egress postures stay supported** via the `egress` knob above:
   `open` serves the token-saving profile, `strict` the security
   profile. Summarization triggers only above the `min_kb` threshold
   (default 4 KB, configurable).
2. **Re-summarize policy**: whichever comes first of a content-drift
   threshold (default 30% of lines changed since the last summarized
   blob) or a change count (default 5 re-indexes of the file). Both
   configurable.
3. **Summaries federate**: read-only and scope-tagged like every other
   finding, per decision 3 of proposal 001.
