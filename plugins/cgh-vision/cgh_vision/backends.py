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
    timeout_s: int = 300,
) -> str:
    """One vision call: the image and the prompt to the active backend,
    deterministic (temperature 0), raw text back."""
    cfg = config or {}
    if backend_kind(cfg) == "openai":
        return _ask_openai(model, image_path, prompt, cfg, timeout_s)
    return _ask_ollama(model, image_path, prompt, cfg, timeout_s)


# cgh-vision's default models, as Ollama's own Hugging Face pull specs.
# `ollama pull hf.co/<repo>:<quant>` fetches the GGUF (and its mmproj for a
# vision model) straight from Hugging Face, which works when the Ollama
# registry is blocked but HF is not. One curated entry per default model;
# an unmapped custom model gets the guidance message instead.
_HF_FALLBACK = {
    "qwen2.5vl:3b": "hf.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M",
    "gemma3:4b": "hf.co/ggml-org/gemma-3-4b-it-GGUF:Q4_K_M",
}


def manual_gguf_steps(model: str) -> str:
    """The by-hand path to register `model` in Ollama when both the
    registry and the automatic Hugging Face pull are unreachable: download
    the weights and the vision projector (mmproj), write a two-line
    Modelfile, `ollama create` under the exact name the profile expects.
    For a known default model the HF repo and quant are filled in; an
    unmapped custom model gets the same shape with placeholders."""
    spec = _HF_FALLBACK.get(model)
    if spec:
        repo_quant = spec.removeprefix("hf.co/")
        repo, _, quant = repo_quant.partition(":")
        quant = quant or "Q4_K_M"
    else:
        repo, quant = "<org>/<Model>-GGUF", "Q4_K_M"
    base = repo.rsplit("/", 1)[-1]
    return (
        f"Register {model!r} by hand (the Ollama registry and the automatic\n"
        "Hugging Face pull are both unreachable):\n\n"
        '  1. pip install -U "huggingface_hub[cli]"\n\n'
        "  2. Download the weights AND the vision projector (mmproj) into one\n"
        "     directory. A vision model needs both: without the mmproj, Ollama\n"
        "     loads a text-only model and ignores every image.\n"
        f'       hf download {repo} --include "*{quant}*" --local-dir models/{base}\n'
        f'       hf download {repo} --include "*mmproj*" --local-dir models/{base}\n\n'
        f"  3. Write models/{base}/Modelfile with the two files you got (exact\n"
        "     names vary by repo), the weights first, then the mmproj:\n"
        "       FROM ./<weights>.gguf\n"
        "       FROM ./<mmproj>.gguf\n\n"
        f"  4. ollama create {model} -f models/{base}/Modelfile\n\n"
        "  5. Re-run. cgh only ever asks Ollama for the name, it downloads\n"
        "     nothing itself."
    )


def fetch_model_from_hf(model: str, cfg: dict) -> bool:
    """Missing Ollama model: fetch it from Hugging Face via Ollama's own
    hf.co pull, then alias it to the expected name so the profile keeps
    working. Returns True when the model is available afterwards. Best
    effort: opt-out (vision_auto_fetch=false), no ollama binary, an
    unmapped model, or a failed pull all return False and the caller
    surfaces the guidance error instead."""
    import shutil
    import subprocess
    import sys

    if not cfg.get("vision_auto_fetch", True):
        return False
    spec = _HF_FALLBACK.get(model)
    if not spec or shutil.which("ollama") is None:
        return False
    print(
        f"[cgh-vision] model {model!r} not in Ollama; fetching it from "
        f"Hugging Face ({spec}). This downloads several GB. "
        "Disable with vision_auto_fetch = false.",
        file=sys.stderr,
        flush=True,
    )
    try:
        # ollama prints its own download progress to the inherited stderr.
        if subprocess.run(["ollama", "pull", spec], timeout=3600).returncode != 0:
            return False
        # Alias hf.co/... to the profile's name so nodes_model resolves now
        # and on the next run. A failed alias just means the retry misses.
        subprocess.run(["ollama", "cp", spec, model], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _ask_ollama(
    model: str,
    image_path: Path,
    prompt: str,
    cfg: dict,
    timeout_s: int,
    _allow_fallback: bool = True,
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
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Model not present. Try to fetch it from Hugging Face via
            # Ollama and retry once; if that is off or fails, name the
            # model and every way to get it, not a raw urllib traceback.
            if _allow_fallback and fetch_model_from_hf(model, cfg):
                return _ask_ollama(
                    model, image_path, prompt, cfg, timeout_s, _allow_fallback=False
                )
            raise VisionError(
                f"Ollama has no model {model!r}, and the automatic Hugging "
                f"Face pull did not resolve it. Fastest fix if the registry "
                f"is reachable: `ollama pull {model}`. Otherwise serve it from "
                "Hugging Face without Ollama via `cgh vision setup --llamacpp`, "
                "or register a local GGUF by hand:\n\n"
                f"{manual_gguf_steps(model)}"
            ) from exc
        detail = exc.read().decode(errors="replace")[:200]
        raise VisionError(f"Ollama error {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        timed_out = (
            isinstance(exc, TimeoutError)
            or isinstance(reason, TimeoutError)
            or "timed out" in str(reason).lower()
        )
        if timed_out:
            # The daemon answered the socket but not within the deadline:
            # the model is loading on first use, or inference is slow (CPU).
            # This is NOT a dead daemon, so do not ask if it is running.
            raise VisionError(
                f"Ollama did not respond within {timeout_s}s at "
                f"{ollama_url(cfg)}. The daemon is up but the request timed "
                f"out: the model {model!r} is likely loading on first use, or "
                "inference is slow on CPU. Warm it once with "
                f"`ollama run {model}`, raise [plugin.vision] timeout_s, or "
                "try --profile fast."
            ) from exc
        raise VisionError(
            f"Ollama unreachable at {ollama_url(cfg)}: {exc}. Is the daemon "
            "running? Start it with `ollama serve`."
        ) from exc
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
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Connection refused, DNS failure, timeout: the endpoint is not
        # answering. Surface it as a VisionError like every other
        # backend failure, never a raw urllib error.
        raise VisionError(f"vision endpoint unreachable: {exc}") from exc
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError(
            f"unexpected vision response shape: {str(data)[:200]}"
        ) from exc
