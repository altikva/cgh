# Summarize with the local-first defaults

`sdk.summarize` picks the first available local backend. Since
cgh-summarize 0.3.0 (frozen) there are no cloud backends at all, so
nothing leaves the machine whatever `cloud_allowed` says.

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
setup, custom `ollama_url`, and the loopback rule: an Ollama on a
non-loopback URL is classified as a **cloud** backend and never used,
so your LAN GPU box is excluded by design.

The script still derives `cloud_allowed` from `sdk.egress_decision`, the
pattern to keep for any model call you make yourself; cgh-summarize
0.3.0 ignores the flag.

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
