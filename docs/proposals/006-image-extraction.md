# Proposal 006: image reading, architecture extraction, anonymized diagrams

Status: draft. Depends on proposal 001 (plugin loader, scanner surface,
finding store), reuses the egress gate from proposal 002 and the
severity conventions from proposal 003.

## Idea

Repos are full of images the graph cannot see: architecture diagrams
(png exports of drawio, Excalidraw, Lucid), infrastructure schemas,
whiteboard photos, screenshots pasted into docs. Today cgh skips them
entirely, so an agent asked "how does the payment flow work" never
benefits from the one diagram that answers it.

The proposal: a first-party plugin, `cgh-vision`, that reads images
with a small vision-language model, extracts the technical structure
(components, links, labels, zones), **anonymizes the extraction**, and
writes the result as markdown plus a Mermaid diagram. The extraction
is stored as findings (FTS-searchable, served through MCP like
summaries) and can also be written next to the image as a sidecar
`.md` the graph indexes like any other doc.

The image itself never has to leave the machine for this to work:
small VLMs run locally on CPU or Apple Silicon. Cloud backends stay
possible for repos where the gate allows them, same policy as
summaries.

## Pipeline

One deferred scanner (never in the indexing hot path), four stages:

1. **Read**: image files matched by extension (`.png`, `.jpg`,
   `.jpeg`, `.webp`; `.svg` is text and goes through a cheaper path,
   parsed then optionally rasterized). Skip below a minimum size,
   skip above a maximum, dedup by blob SHA like every scanner.
2. **Extract**: the VLM is prompted for structure, not prose:
   components with their labels, directed links, groupings/zones,
   technologies recognized from icons (a decent small VLM reads AWS /
   GCP / Azure / k8s icon sets). Output contract is JSON.
3. **Anonymize**: before anything is persisted, the extraction (not
   the image) goes through a scrub pass: project identifiers, IPs and
   CIDRs, hostnames, account IDs, emails, people names are replaced
   with stable placeholders (`project-A`, `10.x.x.x`, `acct-1`). The
   cgh-pii scanner runs over the scrubbed text as a tripwire, same
   pattern as the bugreport payload: if PII survives scrubbing, the
   extraction is dropped and the failure logged, not shipped.
4. **Emit**: two artifacts from the JSON:
   - findings: `diagram.summary` (3-5 sentences), `diagram.mermaid`
     (the generated Mermaid), `diagram.entities` (JSON, for tooling),
     all severity info, all FTS-fed;
   - optionally a sidecar `<image>.extracted.md` (front matter +
     summary + mermaid block) written next to the image and indexed
     by the markdown parser like any other doc. Off by default,
     `sidecar = true` to opt in, because it writes into the user's
     tree.

A `cgh diagram <image>` CLI verb runs the same pipeline on demand for
one file and prints the markdown, so the feature is testable without
waiting for a scan.

## Backends

Same shape as summarize (a `vision.backend` extension namespace,
extras shadow built-ins), because the market moves and Bob taught us
the lesson:

| Backend | What it runs | Egress | Notes |
|---|---|---|---|
| `ollama` | any vision model the daemon serves (`qwen2.5vl`, `gemma3`, `moondream`, `llava`) | local | recommended default when the daemon is present |
| `local` | bundled llama.cpp runner, GGUF downloaded on first use (SmolVLM 500M / Moondream 1.8B / Qwen-VL 2B class) | local | no daemon needed; CPU is enough, Metal free on Apple Silicon; download is SHA-pinned and logged |
| `cli:claude` | `claude -p` with the image attached | cloud | only past the egress gate |
| `cli:gemini` | `gemini -p` with the image | cloud | same |
| `openai` | any OpenAI-compatible endpoint with vision | cloud/local | vLLM and LM Studio serve VLMs locally through this too |

No `structural` fallback here: without a model there is no extraction,
the scanner just skips and records nothing.

## Egress and confidentiality

Images are the most dangerous file class we have touched: a screenshot
can hold credentials, tokens, customer data, faces. Rules:

- The gate treats images as confidential by default in secure mode:
  cloud vision requires an explicit non-confidential label, allowlist
  semantics, no exception.
- In assist mode the standard gate applies (block findings stop cloud,
  PII findings stop cloud unless `allow_pii = true`), with one
  addition: since we cannot scan pixels for secrets cheaply, cloud
  vision is opt-in per repo (`cloud = true` under `[plugin.vision]`),
  not merely gated.
- Local backends bypass the gate as always: nothing leaves.
- Every cloud send is logged to activity, same as summaries.
- Anonymization applies to the extraction regardless of backend: even
  a fully local pipeline should not write raw project IDs and IPs into
  findings that later feed resume bundles and insights, which can
  travel further than the repo.

## What this buys the graph

- `search_docs("payment flow")` can return the extracted markdown of
  the diagram that documents it, with a Mermaid the agent can quote.
- `cgh insights` gains architecture-level material without reading a
  single image at question time.
- The anonymized Mermaid is shareable by construction: pasteable into
  a doc, an issue, or a bug report without leaking the environment.

## Open questions

1. **Plugin split**: one `cgh-vision` plugin (extraction + anonymize +
   emit), or extraction in `cgh-vision` and the anonymize pass as a
   reusable piece of `cgh-pii` that other plugins can call? The scrub
   logic overlaps with what bugreport already does.
2. **Bundled local runner**: is the `local` backend (llama.cpp +
   first-use GGUF download) part of v1, or does v1 ship with Ollama +
   CLIs + OpenAI-compatible only, and the bundled runner comes as its
   own plugin (`cgh-local-llm`) that vision and summarize both use?
   The second keeps cgh-vision small and gives summarize the same
   benefit for free.
3. **Default model**: SmolVLM 500M (fast, weakest), Moondream 1.8B
   (good diagram reading, ~1.5 GB), or Qwen-VL 2B class (best labels,
   heaviest)? Determines the RAM floor.
4. **Sidecar default**: findings-only by default with `sidecar = true`
   opt-in (proposed), or sidecar on by default because a file in the
   tree versions with the repo and reviews like everything else?
5. **drawio/Excalidraw sources**: when the source XML/JSON sits next
   to the exported png, parsing the source directly is lossless and
   free, no model at all. Worth a source-first path in v1, or a
   later refinement?
