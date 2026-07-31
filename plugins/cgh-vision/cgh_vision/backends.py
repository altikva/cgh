# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The vision backend: a local Ollama daemon serving vision
#              models (qwen2.5vl, gemma3, ...). One ask() call sends an
#              image plus a prompt and returns the raw text; egress is
#              "local", nothing leaves the machine. Third-party backends
#              can shadow this through the vision.backend extension
#              namespace later; v1 ships Ollama only, matching the
#              benchmark that selected the default models.

from __future__ import annotations

import base64
import json
import socket
import urllib.request
from pathlib import Path


def ollama_url(config: dict) -> str:
    return str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")


def available(config: dict) -> bool:
    """Fast probe: is the Ollama daemon reachable?"""
    try:
        host_port = ollama_url(config).split("//", 1)[1]
        host, _, port = host_port.partition(":")
        with socket.create_connection((host, int(port or 80)), timeout=0.3):
            return True
    except OSError:
        return False


def ask(
    model: str,
    image_path: Path,
    prompt: str,
    config: dict | None = None,
    timeout_s: int = 120,
) -> str:
    """One vision call: the image and the prompt to a local model,
    deterministic (temperature 0), raw text back."""
    cfg = config or {}
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "images": [base64.b64encode(image_path.read_bytes()).decode()],
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode()
    req = urllib.request.Request(
        f"{ollama_url(cfg)}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        out = json.loads(resp.read().decode())
    return str(out.get("response", ""))
