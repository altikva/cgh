# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Two backends. CliBackend shells out to a configured agent CLI
#              (claude, codex, ...), a cloud egress the flow gates. OllamaBackend
#              calls a local Ollama model, so nothing leaves the machine and the
#              gate is skipped, the leanest, free path for boilerplate.
#              resolve_backend picks between them from config. The four-backend
#              matrix from cgh-summarize is not rebuilt; more come with demand.
#              Every call goes through the Backend protocol, so tests use a fake.

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request

_OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434/api/generate"


class CliBackend:
    """Generate by invoking a configured agent CLI. The agent reaches a
    cloud model, so this is NOT local: the flow runs each reference through
    the egress gate before calling it."""

    is_local = False

    def __init__(self, command: list[str], timeout: float = 120.0) -> None:
        # command is an argv list from config, e.g. ["claude", "-p"]. Never a
        # shell string: no shell=True, so nothing in the prompt is interpreted.
        self._command = list(command)
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"cli:{self._command[0]}" if self._command else "cli"

    def available(self) -> bool:
        return bool(self._command) and shutil.which(self._command[0]) is not None

    def generate(self, system: str, user: str) -> tuple[str, float]:
        from codegraph.plugin_api import quiet_subprocess_kwargs

        prompt = f"{system}\n\n{user}"
        try:
            proc = subprocess.run(
                [*self._command, "--"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                **quiet_subprocess_kwargs(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return "", 0.0
        # Cost is unknown for an agent CLI (it bills its own account); report
        # 0.0 rather than invent a number.
        return proc.stdout or "", 0.0


class OllamaBackend:
    """Generate with a local Ollama model. Nothing leaves the machine, so
    is_local is True and the flow skips the egress gate. The leanest option:
    no cloud round-trip, no per-token cost."""

    is_local = True

    def __init__(
        self, model: str, url: str = _OLLAMA_DEFAULT_URL, timeout: float = 120.0
    ) -> None:
        self._model = model
        self._url = url
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    def available(self) -> bool:
        return bool(self._model)

    def generate(self, system: str, user: str) -> tuple[str, float]:
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": f"{system}\n\n{user}",
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return "", 0.0
        # Local generation is free; report 0.0 cost.
        return body.get("response", ""), 0.0


def resolve_backend(config: dict):
    """The backend to generate with, chosen from config. An explicit
    ``backend = "ollama"`` (or an ``ollama_model`` with no ``command``) selects
    the local model; otherwise a ``command`` selects the agent CLI. Returns
    None when nothing is configured, so the caller reports a clear "no backend"
    instead of guessing."""
    kind = str(config.get("backend", "")).strip().lower()
    if kind == "ollama" or (not config.get("command") and config.get("ollama_model")):
        model = config.get("ollama_model")
        if not model:
            return None
        return OllamaBackend(
            model,
            config.get("ollama_url", _OLLAMA_DEFAULT_URL),
            timeout=float(config.get("timeout", 120.0)),
        )

    command = config.get("command")
    if isinstance(command, str):
        command = command.split()
    if not command:
        return None
    return CliBackend(command, timeout=float(config.get("timeout", 120.0)))
