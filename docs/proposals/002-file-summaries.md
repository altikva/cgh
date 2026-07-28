# Proposal 002: file summaries with an egress gate

Status: draft, for discussion. Depends on proposal 001 (plugin loader,
scanner surface, Finding store) and pairs with cgh-classify.

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

Before any cloud backend sees a file, the gate checks the Finding store:

1. A `confidential` finding on the file: blocked, always.
2. Any finding with severity `block` (e.g. `secret.aws_key`): blocked.
3. `pii.*` findings: blocked by default, `allow_pii = true` to override.
4. No classification at all: allowed by default; `require_label = true`
   switches to allowlist mode where only files explicitly labeled
   non-confidential go out (posture for sensitive orgs).

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

## Open questions

1. Default when no classifier is installed: gate on PII/secrets only
   (proposed above), or refuse cloud backends entirely until
   cgh-classify is set up?
2. Re-summarize policy: on every content change (blob SHA), or only when
   the old summary's SHA drifts by more than a threshold of lines?
3. Should summaries federate to the parent like other findings (read
   only, scope-tagged)? Leaning yes, consistent with 001 decision 3.
