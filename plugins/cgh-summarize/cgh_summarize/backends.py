# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Summarizer backends, local only since 0.3.0. A backend
#              declares a name, an egress class ("local" or "cloud"), an
#              availability probe, and summarize(prompt). Built-ins: a
#              loopback Ollama daemon, a loopback OpenAI-compatible server
#              (llama.cpp, vLLM, LM Studio) and "structural", which
#              returns the outline with no model. Third-party backends
#              join through the summarize.backend extension namespace;
#              any backend that classifies as "cloud" is never picked.

from __future__ import annotations

import json
import socket
import time
import urllib.request

_TIMEOUT = 120  # seconds per model call

# Auto-pick preference when no configured model is installed: generative
# families, small-to-capable first. Embedding models cannot generate text,
# so they are excluded from the pick entirely.
_MODEL_PREF = ("qwen", "gemma", "llama", "mistral", "phi", "granite")
_TAGS_TTL = 60.0
_tags_cache: dict[str, tuple[float, frozenset[str]]] = {}


class SummarizeError(RuntimeError):
    """A backend failed to produce a summary."""


def installed_ollama_models(url: str) -> frozenset[str]:
    """Model names Ollama reports at <url>/api/tags, cached briefly so an
    indexing run does not re-query per file. Empty set on any error (an
    empty set makes the backend read as unavailable, which is correct)."""
    now = time.time()
    hit = _tags_cache.get(url)
    if hit and now - hit[0] < _TAGS_TTL:
        return hit[1]
    names: set[str] = set()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("models", []):
            n = str(m.get("name") or m.get("model") or "").strip()
            if n:
                names.add(n)
    except Exception:
        return frozenset()
    result = frozenset(names)
    _tags_cache[url] = (now, result)
    return result


def resolve_ollama_model(url: str, configured: str) -> str | None:
    """The model to call: the configured one when it is installed,
    otherwise an auto-picked installed generative model (family preference,
    then name order). None when nothing usable is installed, so callers can
    degrade instead of 404-ing on a hardcoded name that was never pulled."""
    installed = installed_ollama_models(url)
    if not installed:
        return None
    if configured and configured in installed:
        return configured
    generative = sorted(m for m in installed if "embed" not in m.lower())
    if not generative:
        return None
    for fam in _MODEL_PREF:
        for m in generative:
            if m.lower().startswith(fam):
                return m
    return generative[0]


def reset_tags_cache() -> None:
    """Test seam: drop the cached /api/tags results."""
    _tags_cache.clear()


def _url_host_port(url: str) -> tuple[str, int] | None:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, parts.port or 80


class StructuralBackend:
    """No model at all: the prompt scaffold IS the summary. Free, local,
    always available; the fallback when nothing else is usable."""

    name = "structural"
    egress = "local"

    def available(self, config: dict) -> bool:
        return True

    def summarize(self, prompt: str, config: dict) -> str:
        # The scaffold arrives as "OUTLINE:\n...\nEXCERPT:\n..."; keep the
        # outline part, which is the structural summary.
        outline = prompt.split("EXCERPT:", 1)[0]
        return outline.replace("OUTLINE:", "", 1).strip()[:2000]


class OllamaBackend:
    """An Ollama daemon. Local only when the URL says so: the egress
    class is computed from the configured host, because a static "local"
    label plus a configurable ollama_url would let content leave the
    machine. A remote URL classifies as cloud and is never picked."""

    name = "ollama"
    egress = "local"  # earned only for loopback URLs, see egress_class

    def _url(self, config: dict) -> str:
        return str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")

    def egress_class(self, config: dict) -> str:
        from codegraph.plugin_api import is_loopback_url

        return "local" if is_loopback_url(self._url(config)) else "cloud"

    def available(self, config: dict) -> bool:
        # The daemon must be reachable AND expose at least one usable
        # (generative) model. A running daemon with no pulled model is not
        # usable: gating on model presence here is what lets the scanner
        # fall through to the next backend (or degrade) instead of 404-ing
        # on every file with a hardcoded model name.
        target = _url_host_port(self._url(config))
        if target is None:
            return False
        try:
            with socket.create_connection(target, timeout=0.3):
                pass
        except OSError:
            return False
        return resolve_ollama_model(self._url(config), self._model(config)) is not None

    def _model(self, config: dict) -> str:
        return str(config.get("ollama_model", "qwen2.5:1.5b"))

    def summarize(self, prompt: str, config: dict) -> str:
        url = self._url(config)
        model = resolve_ollama_model(url, self._model(config))
        if model is None:
            raise SummarizeError(
                "no Ollama model installed to summarize with; pull one "
                "(`ollama pull qwen2.5:1.5b`) or set [plugin.summarize] "
                "ollama_model to a model you have"
            )
        payload = json.dumps(
            {"model": model, "prompt": prompt, "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(
            url + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("response", "").strip()


class OpenAICompatibleBackend:
    """An OpenAI-compatible chat server on this machine: llama.cpp's
    llama-server, vLLM, LM Studio. Like Ollama, "local" is earned by a
    loopback base URL; a remote one classifies as cloud and is never
    picked."""

    name = "openai"
    egress = "local"  # earned only for loopback URLs, see egress_class

    def egress_class(self, config: dict) -> str:
        from codegraph.plugin_api import is_loopback_url

        base = str(config.get("openai_base_url", ""))
        return "local" if base and is_loopback_url(base) else "cloud"

    def available(self, config: dict) -> bool:
        return bool(config.get("openai_base_url")) and bool(config.get("openai_model"))

    def summarize(self, prompt: str, config: dict) -> str:
        import os

        base = str(config["openai_base_url"]).rstrip("/")
        key = os.environ.get(str(config.get("openai_api_key_env", "OPENAI_API_KEY")))
        payload = json.dumps(
            {
                "model": config["openai_model"],
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            base + "/chat/completions", data=payload, headers=headers
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()


# Auto-selection order: the local daemon, then a configured loopback
# endpoint, then the model-free fallback. The agent CLI backends (claude
# -p and friends) were removed in 0.3.0: they sent file content to a
# cloud model on every index.
_BUILTINS = [
    OllamaBackend(),
    OpenAICompatibleBackend(),
    StructuralBackend(),
]


def egress_of(backend, config: dict) -> str:
    """A backend that computes its egress class from the config wins
    over its static label (ollama is only "local" on a loopback URL);
    unknown backends default to "cloud", failing closed."""
    if hasattr(backend, "egress_class"):
        return str(backend.egress_class(config))
    return str(getattr(backend, "egress", "cloud"))


def resolve_backends(config: dict, extras: list | None = None) -> list:
    """Built-ins plus third-party backends from the summarize.backend
    extension namespace, extras first so a dedicated backend can shadow
    a generic one under the same auto-selection."""
    return list(extras or []) + list(_BUILTINS)


def pick_backend(config: dict, extras: list | None = None, cloud_allowed: bool = False):
    """First available local backend, or None. An explicit ``backend``
    config key restricts the choice to that name (still subject to
    availability). ``cloud_allowed`` is accepted for callers written
    against 0.2 (codegraph.sdk.summarize passes it) and ignored: a
    backend classified "cloud" is never picked."""
    del cloud_allowed
    wanted = str(config.get("backend", "auto"))
    for backend in resolve_backends(config, extras):
        if wanted != "auto" and backend.name != wanted:
            continue
        if egress_of(backend, config) == "cloud":
            continue
        try:
            if backend.available(config):
                return backend
        except Exception:
            continue
    return None
