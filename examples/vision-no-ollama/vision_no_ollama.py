# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Run the vision pipeline without Ollama, against any
#              OpenAI-compatible vision server: a local llama.cpp
#              llama-server on GGUF weights, LM Studio, vLLM, or an
#              approved internal gateway. The only change from the
#              Ollama default is the config: set openai_base_url. Egress
#              is judged from that URL, so a loopback server stays local
#              and secure mode is satisfied; a remote gateway is cloud.
#              Requires: pip install cgh cgh-vision, and a running
#              OpenAI-compatible vision endpoint (see the README).

from __future__ import annotations

import sys

from codegraph import sdk

# Point at your server. A loopback llama-server started with
# `cgh vision setup --llamacpp` listens here; change it for LM Studio,
# vLLM or a gateway. openai_api_key_env names the env var holding a key
# when the endpoint needs one.
CONFIG = {
    "openai_base_url": "http://127.0.0.1:8080/v1",
    "nodes_model": "qwen2.5-vl",
    "edges_model": "qwen2.5-vl",
    "fallback_model": "",
    # "openai_api_key_env": "OPENAI_API_KEY",
}


def main(image: str) -> None:
    # Same SDK calls as the Ollama path; only CONFIG differs.
    inv = sdk.image_inventory(image, CONFIG)
    print(f"content: {', '.join(inv['content'])}")

    if {"architecture_diagram", "flowchart"} & set(inv["content"]):
        ex = sdk.extract_diagram(image, CONFIG)
        print(f"{len(ex['nodes'])} nodes, {len(ex['edges'])} edges")
        print(ex["mermaid"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python vision_no_ollama.py <image>")
    main(sys.argv[1])
