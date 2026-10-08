# cgh-summarize

> **Frozen.** 0.3.0 is the final release. `cgh[plugins]` no longer
> installs it, it gets no new features, and it may be removed from the
> cgh repository later. Agents write better summaries than a small
> local model, at the moment they read a file: `cgh artifact note` and
> the `knowledge_record` MCP tool store them in cgh and cover this use
> case. It still installs by name (`pip install cgh-summarize`) and
> keeps working as described below.

Prose summaries of tracked files for
[cgh](https://github.com/altikva/cgh), written by a model running on
your machine, on demand.

```bash
pip install cgh-summarize
cgh summarize status      # detected backends, coverage
cgh summarize run         # summarize the tracked files
cgh findings --key summary
cgh insights              # cross-file patterns from the summaries
```

## What changed in 0.3.0

- **Nothing runs at index time.** Earlier versions summarized files in
  the background on every index. Summaries are now written only by an
  explicit `cgh summarize run`.
- **Local backends only.** The agent CLI backends (`claude -p`,
  `gemini`, `codex`, `bob`) are gone, and so is the egress gate that
  guarded them: no file content leaves the machine. An Ollama or
  OpenAI-compatible URL that is not loopback is never used.

## Backends

| Backend | Runs |
|---|---|
| `ollama` | an Ollama daemon on a loopback URL, model is a config line |
| `openai` | an OpenAI-compatible server on a loopback URL (llama.cpp's `llama-server`, vLLM, LM Studio) |
| `structural` | cgh's own outline, no model at all |

`backend = "auto"` (default) picks the first available in that order.
Third-party plugins add backends through the `summarize.backend`
extension namespace; one that declares `egress = "cloud"` is skipped.

## Configuration

```toml
[plugin.summarize]
# backend = "auto"          # or ollama, openai, structural
# min_kb = 4                # skip files smaller than this
# language = "en"
# ollama_model = "qwen2.5:1.5b"
# ollama_url = "http://127.0.0.1:11434"
# openai_base_url = ""      # e.g. http://127.0.0.1:8080/v1
# openai_model = ""
# openai_api_key_env = "OPENAI_API_KEY"
```

Re-summarize policy: when content changes, the old summary is carried
forward while the drift stays under 30% of lines and fewer than 5
changes accumulated, then a fresh summary is produced.
