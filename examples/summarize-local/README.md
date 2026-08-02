# Summarize with the local-first defaults

`sdk.summarize` picks the first available backend that honors the
egress constraint. The default is the safe one: `cloud_allowed=False`
restricts the pick to local backends, so nothing leaves the machine
unless your own egress decision opens the door.

## Step 1: install

```bash
pip install cgh cgh-summarize
```

Works out of the box with **no model at all**: without any backend,
`sdk.summarize` falls back to an honest excerpt, never an exception.

## Step 2 (optional): a local model via Ollama

```bash
ollama pull qwen2.5:1.5b     # the default summarize model, ~1 GB
```

cgh-summarize does not install Ollama; see the
[vision-pipeline README](../vision-pipeline/README.md) for the daemon
setup, custom `ollama_url`, and the loopback rule: since 0.9.0 an
Ollama on a non-loopback URL is classified as a **cloud** backend, so
`cloud_allowed=False` excludes your LAN GPU box by design.

## Step 3 (optional): cloud backends, behind your gate

Agent CLIs already on your PATH (claude, gemini, codex, bob) and any
OpenAI-compatible endpoint are picked up automatically, but only when
you pass `cloud_allowed=True`, ideally derived from
`sdk.egress_decision` over your scan findings, as the script shows.

## Run

```bash
python summarize_local.py
```

`config.example.toml` lists every backend knob (forcing a backend,
models, endpoints).

## Same result without writing code

**cgh CLI**, inside an indexed repo (`cgh init`):

```bash
cgh summarize status        # backends, egress posture, coverage
cgh summarize run           # summarize the tracked files, gate applied
cgh insights                # cross-file patterns from the summaries
cgh insights --question "where is the payment flow duplicated?"
```

**MCP through your agent**: once summaries exist, the agent calls the
`summaries` tool (per file or corpus-wide) and `corpus_insights`
(question answering over the gate-cleared summaries). The egress gate
applied at summarize time, so what the agent reads never included
content a cloud backend was not allowed to see.

## Tests

`test_summarize_local.py` pins the deterministic `structural` backend
so no daemon, CLI or network is needed:

```bash
pytest examples/summarize-local -q
```
