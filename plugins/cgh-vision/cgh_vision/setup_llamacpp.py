# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh vision setup --llamacpp`: stands up a llama.cpp
#              llama-server serving our default vision model (no Ollama),
#              and points cgh-vision at it through the OpenAI-compatible
#              backend. Installs llama.cpp only through its official
#              channel (brew, or the signed GitHub release binaries), and
#              lets llama-server itself pull the GGUF and its mmproj with
#              -hf. The server is the user's to keep running, exactly
#              like the Ollama daemon: cgh starts it on request but does
#              not supervise it. Validated 2026-08-05: best node/edge
#              scores of any transport (see internal RESULTS.md).

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Our default nodes model, as an official llama.cpp GGUF repo. -hf makes
# llama-server download the weights and the mmproj projector itself. One
# server serves one model, so both passes use it (the benchmark showed
# qwen alone scores best on this transport anyway).
DEFAULT_HF_REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"
DEFAULT_PORT = 8080
_MODEL_NAME = "qwen2.5-vl"


def llamacpp_install(os_name: str = "") -> tuple[str, list[str] | None]:
    """(human hint, argv-to-run-or-None) to install llama.cpp for the
    current OS. argv is None where there is no safe one-liner (Windows
    and Linux get a link, never an auto-run)."""
    name = os_name or os.name
    if name == "nt":
        return (
            "download the signed binaries from "
            "https://github.com/ggml-org/llama.cpp/releases (the "
            "llama-*-bin-win-*.zip asset), and put llama-server.exe on PATH",
            None,
        )
    if sys.platform == "darwin":
        return ("brew install llama.cpp", ["brew", "install", "llama.cpp"])
    return (
        "install llama.cpp from your package manager, or the release "
        "binaries at https://github.com/ggml-org/llama.cpp/releases",
        None,
    )


def _config_block(port: int) -> str:
    return (
        "[plugin.vision]\n"
        f'openai_base_url = "http://127.0.0.1:{port}/v1"\n'
        f'nodes_model = "{_MODEL_NAME}"\n'
        f'edges_model = "{_MODEL_NAME}"\n'
        'fallback_model = ""\n'
    )


def _write_config(console, root: Path, port: int) -> None:
    """Append the [plugin.vision] block to .codegraph/config.toml when
    it has none. Never clobber an existing table: print it instead so
    the user merges by hand."""
    cfg = root / ".codegraph" / "config.toml"
    block = _config_block(port)
    if not cfg.exists():
        console.print(
            f"[dim]no {cfg} yet; run `cgh init` first, then add:[/dim]\n{block}"
        )
        return
    text = cfg.read_text(encoding="utf-8")
    if "[plugin.vision]" in text:
        console.print(
            "[yellow][plugin.vision] already configured.[/yellow] To use "
            f"llama.cpp, set these keys:\n{block}"
        )
        return
    cfg.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")
    console.print(f"[green]+[/green] pointed cgh-vision at llama-server in {cfg}")


def _start_command(port: int) -> list[str]:
    return [
        "llama-server",
        "-hf",
        DEFAULT_HF_REPO,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _offer_to_start(console, root: Path, port: int) -> None:
    """Interactive only: launch llama-server detached. It is the user's
    process afterwards, like the Ollama daemon; cgh does not supervise
    it. Never runs on a non-TTY."""
    if not (sys.stdin and sys.stdin.isatty()):
        return
    argv = _start_command(port)
    console.print(
        f"Start it now (background)? [cyan]{' '.join(argv)}[/cyan] [Y/n] ", end=""
    )
    try:
        if input().strip().lower().startswith("n"):
            return
    except (EOFError, KeyboardInterrupt):
        return
    log = root / ".codegraph" / "llama-server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    from codegraph.plugin_api import quiet_subprocess_kwargs

    kwargs = dict(quiet_subprocess_kwargs())
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        with open(log, "ab") as fh:
            proc = subprocess.Popen(
                argv, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, **kwargs
            )
    except OSError as exc:
        console.print(f"[red]could not start llama-server:[/red] {exc}")
        return
    console.print(
        f"[green]+[/green] llama-server starting (pid {proc.pid}), logging to {log}\n"
        "[dim]  first run downloads the model; it is yours to keep running "
        "and to stop. Re-run cgh vision once it is ready.[/dim]"
    )


def setup_llamacpp(
    config: dict, *, assume_yes: bool = False, port: int = DEFAULT_PORT
) -> None:
    """Wire cgh-vision to a local llama.cpp server instead of Ollama."""
    from rich.console import Console

    from codegraph.plugin_api import find_codegraph_root

    console = Console(stderr=True)
    console.print("[bold]cgh vision setup (llama.cpp, no Ollama)[/bold]")

    if shutil.which("llama-server") is None:
        hint, argv = llamacpp_install()
        console.print("[yellow]llama-server not found.[/yellow] Install llama.cpp:")
        for line in hint.splitlines():
            console.print(f"  [cyan]{line}[/cyan]")
        if argv and shutil.which(argv[0]) and (assume_yes or _yes(console, argv)):
            from codegraph.plugin_api import quiet_subprocess_kwargs

            try:
                subprocess.run(argv, timeout=1800, **quiet_subprocess_kwargs())
            except (OSError, subprocess.TimeoutExpired) as exc:
                console.print(f"[red]install failed:[/red] {exc}")
        if shutil.which("llama-server") is None:
            console.print("[dim]install llama.cpp, then re-run this.[/dim]")
            return

    console.print(
        f"[green]+[/green] llama-server found: {shutil.which('llama-server')}"
    )
    console.print(
        "[dim]It will serve[/dim] "
        f"[cyan]{DEFAULT_HF_REPO}[/cyan] "
        "[dim](weights + mmproj auto-downloaded on first run).[/dim]"
    )

    root = find_codegraph_root(os.getcwd()) or Path(os.getcwd())
    _write_config(console, Path(root), port)
    _offer_to_start(console, Path(root), port)


def _yes(console, argv: list[str]) -> bool:
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    console.print(f"Run [cyan]{' '.join(argv)}[/cyan] now? [Y/n] ", end="")
    try:
        return not input().strip().lower().startswith("n")
    except (EOFError, KeyboardInterrupt):
        return False
