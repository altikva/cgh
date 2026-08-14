# starter-config

A commented `config.toml` you can drop into `.codegraph/` to tune what
cgh indexes and scans. Copy it, edit it, re-index with `cgh index --force`.

## Install

```bash
cgh examples install starter-config --dest .
# then move the config into place and edit it:
#   cp starter-config/config.toml .codegraph/config.toml
```

## What it shows

- Raising `max_file_size_kb` so large PDFs / spreadsheets are indexed
  (the key is in KB: 512000 = 500 MB).
- Turning on the optional PII tiers (`ner`, `llm`) and the summarize
  model, with the auto-pick of an installed Ollama model.
- Narrowing or widening what gets indexed with `ignore_patterns`.

Use `cgh files --check <path>` to see whether a file is indexed and, if
not, why (no parser, over the cap, or an ignore rule).
