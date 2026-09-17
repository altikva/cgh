# Plugins

cgh discovers pip-installed plugins through the `cgh` entry point group: install one, and the next run picks it up. A plugin can add file parsers, per-file scanners, MCP tools, and CLI subcommands through a small versioned API (`codegraph.plugin_api`, contracts documented in its docstrings).

```bash
pip install some-cgh-plugin   # discovery is automatic
cgh plugins                   # list plugins: status, version, surfaces
```

Control which plugins load per repo in `.codegraph/config.toml`:

```toml
[plugins]
# disabled = ["heavy-plugin"]     # skip without uninstalling
# enabled = ["docs", "pii"]       # allowlist mode: load ONLY these

[plugin.pii]                      # per-plugin settings, passed verbatim
# ner = false
```

A broken or incompatible plugin degrades to a warning and a status line in `cgh plugins`, never a crash. Trust model: a plugin is Python executed with cgh's privileges, same as any pytest or flake8 plugin; install what you trust, pin versions, and use allowlist mode on sensitive repos. Plugins licensed under any terms are welcome: see the plugin exception in [LICENSE](./LICENSE).

First-party plugins live in [plugins/](./plugins) and install separately, so the core stays lean; grab all five at once with `pip install "cgh[plugins]"`:

| Plugin | What it adds |
|---|---|
| `cgh-docs` | pdf, docx and xlsx files become searchable sections |
| `cgh-pii` | inline PII and secret detection (emails, IBANs, cards, keys), optional NER and LLM tiers for names and what regex misses |
| `cgh-classify` | human-trainable confidentiality labels + a local classifier |
| `cgh-summarize` | file summaries via your agent CLIs (Claude, Gemini, Codex, IBM Bob), Ollama or any OpenAI-compatible endpoint, plus `cgh insights` |
| `cgh-vision` | image understanding: content inventory, diagram extraction to markdown + Mermaid, table and chart reading, local vision models via Ollama |
| `cgh-bugreport` | crash reports built by allowlist, spooled locally, sent by hand to a private repo |

On a locked-down machine where the Ollama registry is blocked,
`cgh-vision` runs from Hugging Face GGUF weights instead: download the
model plus its vision projector, register it with `ollama create`, and
point the plugin at the name. The plugin prints these exact steps when a
run hits a missing model, and the full walkthrough is in the cgh-vision
README under "When `ollama pull` is blocked".

---

---

[Back to the README](../README.md)
