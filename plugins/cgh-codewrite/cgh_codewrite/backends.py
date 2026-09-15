# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: One real backend to start: CliBackend shells out to a
#              configured agent CLI (claude, codex, ...) and returns its
#              reply. Kept deliberately minimal, the four-backend matrix from
#              cgh-summarize is not rebuilt here; more backends (or a shared
#              layer) come only when there is demand. resolve_backend picks
#              the configured one. Every call goes through the Backend
#              protocol, so the flow and its tests use a fake instead.

from __future__ import annotations

import shutil
import subprocess


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


def resolve_backend(config: dict):
    """The backend to generate with. For now: a CliBackend from the
    configured command. Returns None when nothing is configured, so the
    caller can report a clear "no backend" instead of guessing."""
    command = config.get("command")
    if isinstance(command, str):
        command = command.split()
    if not command:
        return None
    return CliBackend(command, timeout=float(config.get("timeout", 120.0)))
