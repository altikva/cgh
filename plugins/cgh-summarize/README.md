# cgh-summarize

Prose summaries of indexed files for
[cgh](https://github.com/altikva/cgh), produced by whatever model you
already have, behind a confidentiality egress gate.

```bash
pip install cgh-summarize
cgh summarize status      # detected backends, gate posture, coverage
cgh summarize run         # summarize what the gate clears
cgh findings --key summary
cgh insights              # cross-file patterns from the summaries
```

## Backends

| Backend | Runs | Egress |
|---|---|---|
| `cli:claude` | `claude -p`, light model | cloud |
| `cli:gemini` | `gemini -p`, flash tier | cloud |
| `cli:codex` | `codex exec` | cloud |
| `cli:bob` | `bob -p`, IBM BobShell routes the model itself | cloud |
| `ollama` | local Ollama daemon, model is a config line | none |
| `openai` | any OpenAI-compatible endpoint (vLLM, LM Studio, watsonx, ...) | cloud |
| `structural` | cgh's own outline, no model at all | none |

`backend = "auto"` (default) picks the first available in that order.
Third-party plugins add backends through the `summarize.backend`
extension namespace without touching this plugin.

## The egress gate

Before any cloud backend sees a file, its findings are checked: a
`confidential` flag or any block-severity finding (private keys, cloud
credentials) stops it, PII findings stop it unless `allow_pii = true`.
With `mode = "secure"` in the cgh config, the gate switches to
allowlist: only files explicitly labeled non-confidential go out.
Local backends (`ollama`, `structural`) bypass the gate since nothing
leaves the machine. Every cloud call is logged to
`.codegraph/activity.log`.

## Configuration

```toml
[plugin.summarize]
# backend = "auto"          # or cli:claude, cli:gemini, cli:codex,
#                           # cli:bob, ollama, openai, structural
# min_kb = 4                # skip files smaller than this
# allow_pii = false
# language = "en"
# claude_model = "haiku"
# gemini_model = "gemini-2.5-flash"
#                           # no bob_model: BobShell routes the model itself
# ollama_model = "qwen2.5:1.5b"
# ollama_url = "http://127.0.0.1:11434"
# openai_base_url = ""      # e.g. http://localhost:8000/v1
# openai_model = ""
# openai_api_key_env = "OPENAI_API_KEY"
```

Re-summarize policy: unchanged content is never re-scanned (blob SHA);
when content changes, the old summary is carried forward while the
drift stays under 30% of lines and fewer than 5 changes accumulated,
then a fresh summary is produced.
