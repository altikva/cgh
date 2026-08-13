# cgh-vision

Image understanding for [cgh](https://github.com/altikva/cgh): a
content inventory decides what each image contains, then only the
extractors the content warrants run: architecture diagrams become
markdown plus Mermaid, tables and charts become data, dense text
becomes a summary. Everything runs against a local Ollama daemon;
nothing leaves the machine.

```bash
pip install cgh-vision
ollama pull qwen2.5vl:3b gemma3:4b   # the benchmark-selected pair

cgh vision docs/architecture.png                 # markdown on stdout
cgh vision photo.jpg --profile photo             # screen-photo tuning
cgh vision archi.png --out report.md             # also save the report
cgh vision archi.png --format json | jq .diagram # the SDK dicts instead
```

The command shows a progress spinner on stderr while the model passes
run (about 30 s per diagram on Apple Silicon); stdout stays pure
markdown or JSON, pipeable either way.

## Without Ollama, one command: `cgh vision setup --llamacpp`

If Ollama is unavailable and you have llama.cpp (or can install it),
this wires everything up:

```bash
cgh vision setup --llamacpp
```

It finds `llama-server` (offering `brew install llama.cpp` on macOS,
or the signed GitHub-release binaries on Windows, official channels
only), writes a `[plugin.vision]` block pointing cgh-vision at a local
llama-server, and offers to start it. The server pulls our default
vision model (`ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`, weights and
mmproj projector) itself on first run. The server is yours to keep
running and to stop, exactly like the Ollama daemon; cgh starts it on
request but does not supervise it.

Benchmark: llama-server gave the best node and edge scores of any
transport (see the plugin's own notes). This is the recommended
no-Ollama path.

## Without Ollama: any OpenAI-compatible endpoint

Ollama is the default, not a requirement. Setting `openai_base_url`
switches the transport to `/chat/completions` with a base64
`image_url`, which unlocks three things:

- **No daemon at all.** Serve the GGUF weights you downloaded from
  Hugging Face with llama.cpp's own server, which is what Ollama wraps
  anyway:
  ```bash
  llama-server -m qwen2.5-vl-7b-q4_k_m.gguf \
    --mmproj qwen2.5-vl-7b-mmproj-f16.gguf --port 8080
  ```
  ```toml
  [plugin.vision]
  openai_base_url = "http://127.0.0.1:8080/v1"
  nodes_model = "qwen2.5-vl"   # whatever name the server reports
  edges_model = "qwen2.5-vl"
  fallback_model = ""
  ```
  A loopback endpoint stays "local", so secure mode is satisfied and
  nothing leaves the machine.

- **LM Studio, vLLM, or an approved internal gateway**, same config,
  just a different `openai_base_url`. A key is read from the env var
  named by `openai_api_key_env` (default `OPENAI_API_KEY`).

  Node and edge extraction over the OpenAI transport is on par with
  native Ollama; zone detection is a little weaker, because the
  `/chat/completions` template differs from the raw prompt Ollama's
  native API takes. If zones matter most, keep the Ollama backend.

- **Hosted vision models** (a corporate LLM gateway serving qwen-vl,
  GLM-4V, and such). These are non-loopback, so cgh treats them as
  cloud: allowed in assist mode with an audit line, refused in secure
  mode, exactly like a remote Ollama.

## Installing Ollama

`cgh vision` needs the Ollama daemon. It does not bundle it: when the
daemon is unreachable, cgh points at the publisher's official channel
for your OS and, interactively, offers to run it.

| OS | Official channel |
|---|---|
| Windows | `winget install --id Ollama.Ollama -e` (or the signed installer from the Ollama GitHub releases) |
| macOS | `brew install ollama` |
| Linux | `curl -fsSL https://ollama.com/install.sh | sh` (shown, never auto-piped) |

winget is chosen on Windows because managed machines commonly allow it
where a raw `.exe` download is blocked. If your network blocks every
official channel, that is usually a deliberate policy: ask IT to
whitelist Ollama, or point cgh at an approved internal Ollama server
with `ollama_url`. cgh will not mirror or obfuscate the installer to
get it past a content filter.

## When `ollama pull` is blocked

Corporate machines often block the Ollama registry (or the installer
itself). Check first, in one command:

```bash
ollama pull qwen2.5vl:3b   # works? nothing else to read here
```

If it fails, the models can come from Hugging Face instead and be
registered locally. The vision models need two files: the weights and
the **vision projector** (mmproj), which is what makes the model
multimodal; without it Ollama loads a text-only model and every image
is ignored.

The two default models live in official `ggml-org` GGUF repos, weights
and mmproj in the same repo. Download both files, then register each
under the exact name the plugin expects (`qwen2.5vl:3b` for nodes and
edges, `gemma3:4b` for the fallback):

```bash
pip install -U "huggingface_hub[cli]"

# --- Qwen2.5-VL-3B (the default nodes + edges model) ---
hf download ggml-org/Qwen2.5-VL-3B-Instruct-GGUF --include "*Q4_K_M*" \
  --local-dir models/Qwen2.5-VL-3B-Instruct-GGUF
hf download ggml-org/Qwen2.5-VL-3B-Instruct-GGUF --include "*mmproj*" \
  --local-dir models/Qwen2.5-VL-3B-Instruct-GGUF

cat > models/Qwen2.5-VL-3B-Instruct-GGUF/Modelfile <<'EOF'
FROM ./Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf
FROM ./mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf
PARAMETER num_ctx 8192
EOF
ollama create qwen2.5vl:3b -f models/Qwen2.5-VL-3B-Instruct-GGUF/Modelfile

# --- Gemma-3-4b (the default fallback model) ---
hf download ggml-org/gemma-3-4b-it-GGUF --include "*Q4_K_M*" \
  --local-dir models/gemma-3-4b-it-GGUF
hf download ggml-org/gemma-3-4b-it-GGUF --include "*mmproj*" \
  --local-dir models/gemma-3-4b-it-GGUF

cat > models/gemma-3-4b-it-GGUF/Modelfile <<'EOF'
FROM ./gemma-3-4b-it-Q4_K_M.gguf
FROM ./mmproj-model-f16.gguf
PARAMETER num_ctx 8192
EOF
ollama create gemma3:4b -f models/gemma-3-4b-it-GGUF/Modelfile
```

Two things that trip people up:

- **Pass one glob per `hf download`.** `--include "*Q4_K_M*" "*mmproj*"`
  in a single call makes the CLI treat the second pattern as a literal
  filename and it 404s; run two calls into the same `--local-dir`.
- **The Qwen repo ships two mmproj variants** (a `Q8_0` and an `f16`);
  you only need one. The Modelfile above picks the lighter `Q8_0`. Gemma
  ships a single `mmproj-model-f16.gguf`.
- **`PARAMETER num_ctx 8192`** in the Modelfile gives a detailed image
  room in the context window. cgh also sets num_ctx on each request
  (`[plugin.vision] num_ctx`), but baking it in helps when you run the
  model outside cgh. A 400 "exceeds the available context size" means the
  page needs a larger num_ctx.

Confirm each registered model is multimodal, not text-only:

```bash
ollama show qwen2.5vl:3b | grep -iA1 Capabilities   # should list: vision
```

Because these names match the plugin defaults, no config change is
needed. If you register under a different name, point the plugin at it:

```toml
[plugin.vision]
nodes_model = "qwen2.5-vl:custom"
edges_model = "qwen2.5-vl:custom"
fallback_model = ""             # unless you registered a second one
```

`cgh vision --help` and the daemon probe behave identically: cgh only
ever asks Ollama for a model name, it never downloads anything itself.
When a run hits a missing model and the automatic HF pull cannot resolve
it either, the error prints exactly these manual steps for the model in
question, so you do not have to come back here.

## Pipeline

1. **Inventory** (non-directive: an image is never assumed to be a
   diagram; a logo or a photo costs one call and one summary line).
2. **Pre-scaling** for small images: below 1000 px (smaller
   dimension), a 2x Lanczos upscale feeds the diagram passes, which
   the benchmark showed rescues thin-line exports (drawio) without
   ever hurting the others.
3. **Diagram extraction** when warranted: structure with the plain
   contract, enrichment over the found labels (title, kinds,
   technologies, legend only if actually drawn), then a second model
   reads the arrows constrained to the found labels. Benchmarked
   ensemble: node precision 1.00, edge recall 0.80.
4. **Fallback reader** when the structure comes back skeletal (two
   boxes or fewer, or no arrows at all): the arrow model reads the
   structure a second time and wins only if it finds more. The two
   models fail differently, which is what makes the retry worth
   anything: benchmarked over every local vision model, gemma3:4b
   rescues all five thin-line cases the primary reader cannot see
   (2 nodes / 1 edge becoming 13 / 27), needs no extra download,
   and never fires on images already read correctly.
5. **Table / chart / text extractors** as routed.
6. **Post-processing**: fuzzy-duplicate merge, arrow annotations
   dropped from node lists, reversed-edge dedup, and identity
   separation: IPs, CIDRs, FQDNs, emails and server names split out
   of labels, recorded as `pii.image_identity` findings so the
   secure-at-rest layer pseudonymizes them.

## Findings

| Key | Content |
|---|---|
| `image.content` | detected types (`architecture_diagram,table,...`) |
| `image.summary` | one-sentence description, FTS-searchable |
| `diagram.mermaid` | the generated Mermaid |
| `diagram.entities` | nodes/edges/zones as JSON |
| `table.markdown` / `chart.markdown` | extracted data |
| `text.summary` | dense-text summary |
| `pii.image_identity` | identities read off the image (warn) |

## Configuration

```toml
[plugin.vision]
# profile = "default"        # default | fast (single call) | photo
# nodes_model = "qwen2.5vl:3b"
# edges_model = "gemma3:4b"  # set to "" to disable the edge pass
# ollama_url = "http://127.0.0.1:11434"
# timeout_s = 120
# fallback_model = "gemma3:4b"     # second reader, "" disables it
# prescale = true            # 2x upscale of small images (see Pipeline)
# prescale_min_px = 1000     # apply below this smaller-dimension size
# min_bytes = 5120           # skip icons and badges
# max_bytes = 20971520
```

## Embedding

The pipeline is part of the cgh SDK surface (`codegraph.sdk.image_*`,
MIT): `image_inventory`, `extract_diagram`, `extract_table`,
`extract_chart`. See cgh's `docs/EMBEDDING.md`.
