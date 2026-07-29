# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Summarizer backends. A backend declares a name, an egress
#              class ("cloud" or "local", which is what the gate reads),
#              an availability probe, and summarize(prompt). Built-ins:
#              the agent CLIs (claude, gemini, codex) in headless mode,
#              a local Ollama daemon, any OpenAI-compatible endpoint,
#              and "structural" which returns the outline with no model.
#              Third-party backends join through the summarize.backend
#              extension namespace.

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.request

_TIMEOUT = 120  # seconds per model call


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


class CliBackend:
    """An agent CLI in headless mode. Uses the CLI's own auth and billing."""

    egress = "cloud"

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.name = f"cli:{tool}"

    def _command(self, prompt: str, config: dict) -> list[str]:
        if self.tool == "claude":
            model = config.get("claude_model", "haiku")
            return ["claude", "-p", prompt, "--model", str(model)]
        if self.tool == "gemini":
            model = config.get("gemini_model", "gemini-2.5-flash")
            return ["gemini", "-m", str(model), "-p", prompt]
        # codex
        return ["codex", "exec", prompt]

    def available(self, config: dict) -> bool:
        return shutil.which(self.tool) is not None

    def summarize(self, prompt: str, config: dict) -> str:
        proc = subprocess.run(
            self._command(prompt, config),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{self.tool} exited {proc.returncode}: {proc.stderr.strip()[:200]}"
            )
        return proc.stdout.strip()


class OllamaBackend:
    """A local Ollama daemon: the model is one config line away, and
    nothing leaves the machine."""

    name = "ollama"
    egress = "local"

    def _url(self, config: dict) -> str:
        return str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")

    def available(self, config: dict) -> bool:
        try:
            host_port = self._url(config).split("//", 1)[1]
            host, _, port = host_port.partition(":")
            with socket.create_connection((host, int(port or 80)), timeout=0.3):
                return True
        except OSError:
            return False

    def summarize(self, prompt: str, config: dict) -> str:
        payload = json.dumps(
            {
                "model": config.get("ollama_model", "qwen2.5:1.5b"),
                "prompt": prompt,
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url(config) + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("response", "").strip()


class OpenAICompatibleBackend:
    """Any OpenAI-compatible chat endpoint: vLLM, LM Studio, watsonx,
    hosted APIs. base_url plus a key env var covers most of the market."""

    name = "openai"
    egress = "cloud"

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


# Auto-selection order: agent CLIs first (already authenticated, light
# models), then the local daemon, then a configured endpoint, then the
# model-free fallback.
_BUILTINS = [
    CliBackend("claude"),
    CliBackend("gemini"),
    CliBackend("codex"),
    OllamaBackend(),
    OpenAICompatibleBackend(),
    StructuralBackend(),
]


def resolve_backends(config: dict, extras: list | None = None) -> list:
    """Built-ins plus third-party backends from the summarize.backend
    extension namespace, extras first so a dedicated backend can shadow
    a generic one under the same auto-selection."""
    return list(extras or []) + list(_BUILTINS)


def pick_backend(config: dict, extras: list | None = None, cloud_allowed: bool = True):
    """First available backend honoring the egress constraint, or None.
    An explicit ``backend`` config key restricts the choice to that name
    (still subject to the egress constraint and availability)."""
    wanted = str(config.get("backend", "auto"))
    for backend in resolve_backends(config, extras):
        if wanted != "auto" and backend.name != wanted:
            continue
        if not cloud_allowed and getattr(backend, "egress", "cloud") == "cloud":
            continue
        try:
            if backend.available(config):
                return backend
        except Exception:
            continue
    return None
