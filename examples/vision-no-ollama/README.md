# Vision without Ollama

cgh-vision defaults to a local Ollama daemon, but it does not require
one: it speaks the OpenAI-compatible `/chat/completions` API too. That
means you can serve the vision model with llama.cpp directly (no
Ollama, no `ollama.exe`), or reach LM Studio, vLLM, or an approved
internal gateway. The only change from the default is the config.

## Option A: one command with llama.cpp

If Ollama is unavailable, this stands up a local llama.cpp server and
wires cgh-vision to it:

```bash
cgh vision setup --llamacpp
```

It finds `llama-server` (offering `brew install llama.cpp` on macOS, or
the signed GitHub-release binaries on Windows), writes the
`[plugin.vision]` config, and offers to start the server. The server
downloads our default vision model (weights + mmproj) itself on first
run. It stays your process to keep running and stop, like the Ollama
daemon.

## Option B: point at any OpenAI-compatible server yourself

Serve GGUF weights with llama.cpp's own server (this is what Ollama
wraps anyway):

```bash
llama-server -m qwen2.5-vl-7b-q4_k_m.gguf \
  --mmproj qwen2.5-vl-7b-mmproj-f16.gguf --host 127.0.0.1 --port 8080
```

Then set `openai_base_url` in the config. `vision_no_ollama.py` does it
inline; in a cgh repo it goes under `[plugin.vision]` in
`.codegraph/config.toml`:

```toml
[plugin.vision]
openai_base_url = "http://127.0.0.1:8080/v1"
nodes_model = "qwen2.5-vl"
edges_model = "qwen2.5-vl"
```

Same for LM Studio, vLLM, or a gateway: just a different URL, and
`openai_api_key_env` naming the env var with the key when one is
needed.

## Run

```bash
pip install cgh cgh-vision
python vision_no_ollama.py path/to/diagram.png
```

## Egress stays correct

cgh judges "local" from the endpoint URL, not the backend name. A
loopback llama-server (127.0.0.1) is local, so secure mode is
satisfied and image bytes never leave the machine. A remote gateway is
cloud: allowed in assist mode with an audit line, refused in secure
mode, exactly like a remote Ollama. Keep confidential images on a
loopback server.

## What you give up versus Ollama

This path is a real alternative, not a free lunch. Weigh it:

- **Zone detection is weaker.** The `/chat/completions` endpoint wraps
  the prompt in a chat template that the native Ollama API does not, and
  zones are the most template-sensitive part (measured 0.80 to 0.40 on
  the synthetic corpus). Node and edge extraction, the core output,
  held up or improved.
- **You manage the server.** Ollama installs a daemon that starts on
  boot, keeps models warm, swaps models on demand and restarts itself.
  A llama-server you start is a bare process: one model per instance
  (so `nodes_model` and `edges_model` point at the same served model,
  which is why the default two-model pair collapses to one here), no
  auto-restart, no warm pool. `cgh vision setup --llamacpp` starts it
  but does not supervise it.
- **First-call latency.** Nothing keeps the model resident between
  runs unless you keep the server up; a cold start reloads the weights.
- **Model naming is manual.** The server does not know Ollama's tag
  names; you set `nodes_model` to whatever your server reports, and a
  wrong name (or a non-vision GGUF loaded without its mmproj) fails or
  silently ignores the image.
- **Setup is heavier than `ollama pull`.** llama.cpp is a binary to
  install (or build), and you fetch the GGUF plus its mmproj projector
  yourself (llama-server's `-hf` can do it). On a locked-down network
  this may itself be blocked.

Rule of thumb: if Ollama works on your machine, use it. Reach for this
option when Ollama is blocked, when you must run through an approved
internal gateway, or when you already operate an OpenAI-compatible
vision server.

## Tests

`test_vision_no_ollama.py` runs without any server: it fakes the
transport and asserts the OpenAI backend is selected and the payload
is a `/chat/completions` request.

```bash
pytest examples/vision-no-ollama -q
```
