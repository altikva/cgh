# Vision pipeline: image to structure, fully local

Turn an image into structured knowledge with the `codegraph.sdk`
image functions: a content inventory first (never assume a diagram),
then only the extractors the content warrants. Architecture schemas
come back as markdown + Mermaid with identities (IPs, hostnames,
FQDNs) separated from labels so you can pseudonymize them.

## Step 1: install cgh and the vision plugin

```bash
pip install cgh cgh-vision
# or with uv:
uv pip install cgh cgh-vision
```

`cgh-vision` is a plugin: installing it is enough for `codegraph.sdk`
to discover it. No configuration file is needed for the defaults.

## Step 2: install Ollama and pull the models

cgh-vision does **not** install Ollama or download models for you. It
talks to an Ollama daemon over HTTP and fails with a clear error when
none is reachable. One-time setup:

```bash
# macOS
brew install ollama          # or download from https://ollama.com
# Linux
curl -fsSL https://ollama.com/install.sh | sh

ollama serve &               # skip if you run the desktop app

# the two models of the default profile (~6 GB total, one-time)
ollama pull qwen2.5vl:3b
ollama pull gemma3:4b
```

Sanity check: `curl -s http://127.0.0.1:11434/api/tags` lists the
pulled models. No GPU required; on Apple Silicon a diagram takes
about 30 s with the default profile.

**If Ollama is missing or down**, the SDK raises
`cgh_vision.backends.VisionError` ("Ollama daemon not reachable").
Catch it if you want a degraded path; nothing is sent anywhere.

## Step 3: run

```bash
python vision_pipeline.py path/to/diagram.png
```

## Using an existing Ollama server

Every SDK image function accepts a config dict; point `ollama_url` at
the daemon you already run:

```python
config = {"ollama_url": "http://127.0.0.1:11500"}   # non-default port
inv = sdk.image_inventory("diagram.png", config)
```

Read `config.example.toml` for every knob (profile, models, timeout).

**Egress warning**: only a loopback URL keeps the promise that image
bytes never leave the machine. If you point `ollama_url` at another
host (a GPU box on the LAN, a hosted endpoint), the raw image travels
there. Inside a cgh repo the vision scanner refuses non-loopback URLs
in secure mode and audit-logs them in assist mode; when embedding
through the SDK, that responsibility is yours. Keep confidential
images on loopback.

## Changing models or profiles

Three built-in profiles: `default` (qwen nodes + gemma edges, three
passes, best quality), `fast` (single pass), `photo` (tuned for
screen photos). Any Ollama vision model can replace the defaults:

```python
sdk.extract_diagram(img, {"profile": "fast"})
sdk.extract_diagram(img, {"nodes_model": "qwen2.5vl:7b", "timeout_s": 180})
```

## Same result without writing code

**cgh CLI**, one-off on any image:

```bash
cgh vision path/to/diagram.png            # default profile, progress on stderr
cgh vision photo.jpg --profile photo
cgh vision diagram.png --out report.md    # also save the markdown
cgh vision diagram.png --format json      # the SDK dicts on stdout
cgh vision diagram.png --format json | jq '.diagram.nodes[].label'
```

Small images (under 1000 px) are upscaled 2x before extraction, the
benchmarked fix for thin-line drawio exports; `config.example.toml`
shows the `prescale` knobs.

Inside an **indexed repo** (`cgh init`), the deferred scanner runs the
same pipeline on every committed image during indexing; the results
are findings:

```bash
cgh findings                              # image.content, diagram.mermaid, ...
```

**MCP through your agent** (Claude Code, Cursor, Codex with the cgh
MCP server connected): the extractions are already in the finding
store, so the agent does not run models at question time. Ask
something like "what does the architecture diagram in docs/ show?"
and the agent calls the `findings` tool (key `diagram.mermaid` or
`diagram.entities`) or `fts_search` to pull the extracted structure.
In secure mode the identities it sees are pseudonyms.

## Tests

`test_vision_pipeline.py` runs without Ollama: it fakes the model
transport (`cgh_vision.pipeline.ask`) and checks the routing and the
extraction contract. Use the same seam in your own test suite.

```bash
pytest examples/vision-pipeline -q
```
