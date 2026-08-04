# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-04
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Helps install the Ollama daemon through its official
#              channel for the current OS: winget on Windows (managed
#              machines allow it where a raw .exe download is blocked),
#              Homebrew on macOS, the vendor script on Linux. cgh never
#              bundles, mirrors or obfuscates the binary: it only points
#              at, or on request runs, the publisher's own installer. On
#              a locked-down network where even these are blocked, the
#              honest answer is IT approval or a pre-approved internal
#              Ollama server (config ollama_url), not smuggling the
#              executable past the controls.

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def official_install(os_name: str = "") -> tuple[str, list[str] | None]:
    """Return (human hint, argv-to-run-or-None) for the current OS.

    argv is None when there is nothing safe to run non-interactively
    (Linux pipes a vendor script; we show it, never auto-pipe it)."""
    name = os_name or os.name
    plat = sys.platform
    if name == "nt":
        # winget ships with Windows 10/11 and is commonly allowed by
        # enterprise policy where a direct .exe download is not.
        return (
            "winget install --id Ollama.Ollama -e\n"
            "  (or the signed installer from https://github.com/ollama/ollama/releases)",
            ["winget", "install", "--id", "Ollama.Ollama", "-e"],
        )
    if plat == "darwin":
        return ("brew install ollama", ["brew", "install", "ollama"])
    return (
        "curl -fsSL https://ollama.com/install.sh | sh\n"
        "  (review the script first; cgh will not pipe it for you)",
        None,
    )


def print_install_help(console, *, ollama_url: str) -> None:
    """Explain that the daemon is unreachable and how to install it
    through the official channel, without running anything."""
    hint, _argv = official_install()
    console.print(
        f"[yellow]Ollama is not reachable at[/yellow] {ollama_url}\n"
        "[bold]Install it through its official channel:[/bold]"
    )
    for line in hint.splitlines():
        console.print(f"  [cyan]{line}[/cyan]")
    console.print(
        "[dim]Then start it (the desktop app, or `ollama serve`) and pull the "
        "models: ollama pull qwen2.5vl:3b gemma3:4b[/dim]\n"
        "[dim]On a network that blocks even this: ask IT to whitelist Ollama, "
        "or point cgh at an approved internal server with the ollama_url "
        "config. cgh does not bundle or obfuscate the installer.[/dim]"
    )


def offer_to_install(console) -> bool:
    """Interactive only: offer to run the official installer for the
    current OS. Returns True if it ran and succeeded. Never runs on a
    non-TTY (CI, pipes), never auto-pipes a remote script."""
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    _hint, argv = official_install()
    if argv is None:
        return False  # Linux: show the script, do not run it for them
    tool = argv[0]
    if shutil.which(tool) is None:
        console.print(f"[dim]{tool} not found; run the command above by hand.[/dim]")
        return False
    console.print(f"Run [cyan]{' '.join(argv)}[/cyan] now? [Y/n] ", end="")
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer.startswith("n"):
        return False
    from codegraph.plugin_api import quiet_subprocess_kwargs

    try:
        proc = subprocess.run(argv, timeout=900, **quiet_subprocess_kwargs())
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.print(f"[red]install failed:[/red] {exc}")
        return False
    if proc.returncode != 0:
        console.print(f"[red]{tool} exited {proc.returncode}.[/red]")
        return False
    console.print("[green]Ollama installed.[/green] Start it, then re-run cgh vision.")
    return True
