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


class VisionError(RuntimeError):
    """The vision backend cannot run or refused to (egress policy)."""


def ollama_url(config: dict) -> str:
    return str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")


def is_local(config: dict) -> bool:
    """Does the configured daemon live on this machine? The docstring
    promise "nothing leaves the machine" is only true for loopback URLs,
    so the secure posture checks this, not the backend's word."""
    from codegraph.plugin_api import is_loopback_url

    return is_loopback_url(ollama_url(config))


def installed_models(config: dict, timeout_s: float = 2.0) -> set[str]:
    """Model names the daemon can serve right now. Empty when the
    daemon answers nothing usable; callers treat that as unknown
    rather than as absence."""
    try:
        with urllib.request.urlopen(
            f"{ollama_url(config)}/api/tags", timeout=timeout_s
        ) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return set()
    return {str(m.get("name", "")) for m in data.get("models") or []}


def missing_models(config: dict, wanted: list[str]) -> list[str]:
    """Which of these the daemon does not have. A model registered
    locally (ollama create from a GGUF) counts as present: cgh only
    ever asks for a name, it never downloads anything."""
    have = installed_models(config)
    if not have:
        return []
    return [m for m in wanted if m and m not in have]


def available(config: dict) -> bool:
    """Fast probe: is the Ollama daemon reachable?"""
    from urllib.parse import urlsplit

    url = ollama_url(config)
    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
    except ValueError:
        return False
    if not parts.hostname:
        return False
    try:
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=0.3):
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
