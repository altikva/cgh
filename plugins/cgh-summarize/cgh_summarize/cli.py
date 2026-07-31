# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI verbs: `cgh summarize status|run` and `cgh insights`.
#              run walks the tracked, parser-supported files and invokes
#              the scanner synchronously with a progress line; status
#              shows backends, posture and coverage.

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def make_cli_registrar(config: dict, extras_fn):
    def add_cli(sub) -> None:
        p = sub.add_parser(
            "summarize", help="Summarize indexed files behind the egress gate"
        )
        p.add_argument("action", nargs="?", default="status", choices=["status", "run"])
        p.add_argument("--root", default=os.getcwd())
        p.add_argument("--limit", type=int, default=0, help="Cap files this run")
        p.set_defaults(func=lambda args: _cmd_summarize(args, config, extras_fn))

        p = sub.add_parser(
            "insights", help="Cross-file patterns from the gate-cleared summaries"
        )
        p.add_argument("--root", default=os.getcwd())
        p.add_argument(
            "--question", default="", help="Ask the corpus something specific"
        )
        p.set_defaults(func=lambda args: _cmd_insights(args, config, extras_fn))

    return add_cli


def _tracked_supported_files(root: Path) -> list[Path]:
    from codegraph.parsers import is_supported

    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [root / line for line in out.splitlines() if line and is_supported(line)]


def _cmd_summarize(args, config: dict, extras_fn) -> None:
    from rich.console import Console

    from .backends import resolve_backends
    from .gate import egress_posture
    from .scanner import SummarizeScanner

    console = Console()
    root = Path(os.path.abspath(args.root))

    if args.action == "status":
        from codegraph.state.findings import query_findings

        posture = egress_posture(root, config)
        console.print(f"[bold]egress posture:[/bold] {posture}")
        for backend in resolve_backends(config, list(extras_fn())):
            try:
                ok = backend.available(config)
            except Exception:
                ok = False
            state = "[green]available[/green]" if ok else "[dim]unavailable[/dim]"
            console.print(
                f"  {backend.name:<14} {state}  [dim]egress: {backend.egress}[/dim]"
            )
        done = {
            r["file"]
            for r in query_findings(root, key_prefix="summary", limit=10000)
            if r["key"] == "summary"
        }
        console.print(f"[bold]summaries recorded:[/bold] {len(done)} file(s)")
        return

    # run
    scanner = SummarizeScanner(config, root, extras_fn=extras_fn)
    from codegraph.state.findings import record_findings
    from codegraph.state.scan_meta import git_hash_object

    files = _tracked_supported_files(root)
    if args.limit:
        files = files[: args.limit]
    written = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = scanner.scan(path, text, None)
        if not found:
            continue
        try:
            sha = git_hash_object(root, path) or ""
        except Exception:
            sha = ""
        record_findings(root, str(path), scanner.name, found, blob_sha=sha)
        written += 1
        console.print(f"  [green]+[/green] {path.relative_to(root)}")
    console.print(f"[bold]{written}[/bold] file(s) summarized.")


def _cmd_insights(args, config: dict, extras_fn) -> None:
    from rich.console import Console
    from rich.panel import Panel

    from .insights import run_insights

    console = Console()
    root = Path(os.path.abspath(args.root))
    result = run_insights(root, config, extras_fn=extras_fn, question=args.question)
    if "error" in result:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return
    console.print(
        Panel(
            result["text"],
            title=f"Corpus insights ({result['files']} files, {result['backend']})",
            border_style="cyan",
        )
    )
    if result["excluded"]:
        console.print(
            f"[dim]{result['excluded']} summarized file(s) withheld by the "
            "egress gate.[/dim]"
        )
    console.print("[dim]Saved to the knowledge store (tags: insights).[/dim]")
