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
# enabled = ["docs", "codegen"]   # allowlist mode: load ONLY these

[plugin.pii]                      # per-plugin settings, passed verbatim
# scan_on_index = false
```

A broken or incompatible plugin degrades to a warning and a status line in `cgh plugins`, never a crash. Trust model: a plugin is Python executed with cgh's privileges, same as any pytest or flake8 plugin; install what you trust, pin versions, and use allowlist mode on sensitive repos. Plugins licensed under any terms are welcome: see the plugin exception in [LICENSE](./LICENSE).

First-party plugins live in [plugins/](../plugins) and install separately, so the core stays lean. `pip install "cgh[plugins]"` brings the first three:

| Plugin | What it adds | Install |
|---|---|---|
| `cgh-docs` | pdf, docx and xlsx files become searchable sections | `cgh[plugins]` |
| `cgh-codegen` | boilerplate written by a cheap model, mirroring a reference file cgh picks from the graph | `cgh[plugins]` |
| `cgh-bugreport` | crash reports built by allowlist, spooled locally, sent by hand to a private repo | `cgh[plugins]` |
| `cgh-pii` | `cgh pii scan` for secrets on demand (exit 1 on a private or cloud key, for CI), `cgh pii redact` for documents | by name |
| `cgh-vision` | `cgh vision <file>`: content inventory, diagram extraction to markdown + Mermaid, table and chart reading, local vision models | by name |
| `cgh-summarize` | frozen: local-model file summaries, `cgh summarize run` | by name |
| `cgh-classify` | frozen: human-trainable confidentiality labels | by name |

cgh-pii, cgh-vision and cgh-classify do not scan while cgh indexes unless
you set `scan_on_index = true` in their `[plugin.<name>]` table (see
[CONFIGURATION.md](CONFIGURATION.md)); cgh-summarize never does. The
plugin interface for index-time scanners is unchanged, for any plugin
that wants one.

On a locked-down machine where the Ollama registry is blocked,
`cgh-vision` runs from Hugging Face GGUF weights instead: download the
model plus its vision projector, register it with `ollama create`, and
point the plugin at the name. The plugin prints these exact steps when a
run hits a missing model, and the full walkthrough is in the cgh-vision
README under "When `ollama pull` is blocked".

---

---

[Back to the README](../README.md)
