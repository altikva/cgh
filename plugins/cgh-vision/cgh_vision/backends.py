# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The vision transport, two backends behind one ask().
#              Default is a local Ollama daemon (/api/generate). Setting
#              openai_base_url switches to any OpenAI-compatible vision
#              endpoint (/chat/completions with an image_url data URI):
#              llama-server run directly on GGUF weights, so no Ollama is
#              needed at all; LM Studio or vLLM; or an approved internal
#              gateway. Egress is judged from the ACTIVE endpoint URL,
#              not the backend's word, so a loopback llama-server stays
#              "local" and a remote gateway is "cloud" and gated.

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path


class VisionError(RuntimeError):
    """The vision backend cannot run or refused to (egress policy)."""


def backend_kind(config: dict) -> str:
    """ "openai" when an OpenAI-compatible endpoint is configured,
    "ollama" otherwise. An explicit vision_backend key wins."""
    explicit = str(config.get("vision_backend", "")).strip().lower()
    if explicit in ("ollama", "openai"):
        return explicit
    return "openai" if config.get("openai_base_url") else "ollama"


def ollama_url(config: dict) -> str:
    return str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")


def _openai_base(config: dict) -> str:
    return str(config.get("openai_base_url", "")).rstrip("/")


def endpoint_url(config: dict) -> str:
    """The URL of the active backend, for probing and egress checks."""
    return (
        _openai_base(config) if backend_kind(config) == "openai" else ollama_url(config)
    )


def is_local(config: dict) -> bool:
    """Does the active endpoint live on this machine? The promise
    'nothing leaves the machine' is only true for a loopback URL, so
    the secure posture checks the real endpoint, not the backend name.
    A llama-server on 127.0.0.1 is local; a cloud gateway is not."""
    from codegraph.plugin_api import is_loopback_url

    return is_loopback_url(endpoint_url(config))


def _host_port(url: str) -> tuple[str, int] | None:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, parts.port or (443 if url.startswith("https") else 80)


def available(config: dict) -> bool:
    """Fast probe: is the active endpoint reachable?"""
    target = _host_port(endpoint_url(config))
    if target is None:
        return False
    try:
        with socket.create_connection(target, timeout=0.3):
            return True
    except OSError:
        return False


def installed_models(config: dict, timeout_s: float = 2.0) -> set[str]:
    """Model names the backend can serve right now. Empty means unknown,
    never absence: OpenAI-compatible servers expose /models
    inconsistently, so a caller must not read empty as missing."""
    if backend_kind(config) == "openai":
        try:
            with urllib.request.urlopen(
                f"{_openai_base(config)}/models", timeout=timeout_s
            ) as resp:
                data = json.loads(resp.read().decode())
            return {str(m.get("id", "")) for m in data.get("data") or []}
        except Exception:
            return set()
    try:
        with urllib.request.urlopen(
            f"{ollama_url(config)}/api/tags", timeout=timeout_s
        ) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return set()
    return {str(m.get("name", "")) for m in data.get("models") or []}


def missing_models(config: dict, wanted: list[str]) -> list[str]:
    """Which of these the backend does not have. A model registered
    locally (ollama create from a GGUF, or a llama-server loaded with
    one) counts as present: cgh only ever asks for a name."""
    have = installed_models(config)
    if not have:
        return []
    return [m for m in wanted if m and m not in have]


def ask(
    model: str,
    image_path: Path,
    prompt: str,
    config: dict | None = None,
    timeout_s: int = 120,
) -> str:
    """One vision call: the image and the prompt to the active backend,
    deterministic (temperature 0), raw text back."""
    cfg = config or {}
    if backend_kind(cfg) == "openai":
        return _ask_openai(model, image_path, prompt, cfg, timeout_s)
    return _ask_ollama(model, image_path, prompt, cfg, timeout_s)


def _ask_ollama(
    model: str, image_path: Path, prompt: str, cfg: dict, timeout_s: int
) -> str:
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


def _ask_openai(
    model: str, image_path: Path, prompt: str, cfg: dict, timeout_s: int
) -> str:
    """OpenAI-compatible chat completion with a base64 image_url. Works
    with llama-server (GGUF, no Ollama), LM Studio, vLLM, or an internal
    gateway. The model name comes from the profile, as with Ollama."""
    import os

    b64 = base64.b64encode(image_path.read_bytes()).decode()
    suffix = image_path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(str(cfg.get("openai_api_key_env", "OPENAI_API_KEY")))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{_openai_base(cfg)}/chat/completions", data=payload, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise VisionError(f"vision endpoint {exc.code}: {detail}") from exc
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError(
            f"unexpected vision response shape: {str(data)[:200]}"
        ) from exc
