# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI verbs `cgh codewrite pick` (report the reference to mirror)
#              and `cgh codewrite gen` (generate the file from a spec plus that
#              reference, behind the egress gate, refusing to clobber without
#              --force).

from __future__ import annotations

import os
from pathlib import Path


def make_cli_registrar(config: dict):
    def add_cli(sub) -> None:
        p = sub.add_parser("codewrite", help="Pattern-matched code generation helpers")
        actions = p.add_subparsers(dest="cw_action")

        pick = actions.add_parser(
            "pick", help="Show the reference file to mirror for a target"
        )
        pick.add_argument("--target", required=True, help="File you intend to write")
        pick.add_argument(
            "--reference", default="", help="Force a reference instead of picking one"
        )
        pick.add_argument("--root", default=os.getcwd())
        pick.set_defaults(func=lambda args: _cmd_pick(args, config))

        gen = actions.add_parser(
            "gen", help="Generate a file from a spec, mirroring a reference"
        )
        gen.add_argument("--spec", required=True, help="What to generate")
        gen.add_argument("--target", required=True, help="File to write")
        gen.add_argument(
            "--reference", default="", help="Force a reference instead of picking one"
        )
        gen.add_argument(
            "--force", action="store_true", help="Overwrite the target if it exists"
        )
        gen.add_argument(
            "--stdout", action="store_true", help="Print the code, do not write a file"
        )
        gen.add_argument("--root", default=os.getcwd())
        gen.set_defaults(func=lambda args: _cmd_gen(args, config))

    return add_cli


def _cmd_pick(args, config: dict) -> None:
    from rich.console import Console

    from .picker import CodeWriteError, pick_reference

    console = Console()
    root = Path(os.path.abspath(args.root))
    try:
        result = pick_reference(root, args.target, args.reference or None)
    except CodeWriteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if result["reference"] is None:
        console.print(f"[yellow]{result['reason']}[/yellow]")
        if result["graph_available"] is False:
            console.print(
                "[dim]graph unavailable (no index, or an owner holds the "
                "lock); picked from the filesystem only.[/dim]"
            )
        return

    console.print(f"[bold]reference:[/bold] {result['reference']}")
    console.print(f"[dim]{result['reason']}[/dim]")
    others = [c for c in result["candidates"] if c != result["reference"]]
    if others:
        console.print("[dim]other candidates: " + ", ".join(others) + "[/dim]")
    if result["graph_available"] is False:
        console.print("[dim]graph unavailable; filesystem-only pick.[/dim]")


def _cmd_gen(args, config: dict) -> None:
    from rich.console import Console

    from .backends import resolve_backend
    from .flow import run_generation
    from .generate import GenerationError
    from .picker import CodeWriteError

    console = Console()
    root = Path(os.path.abspath(args.root))

    backend = resolve_backend(config)
    if backend is None:
        console.print(
            "[red]no backend configured.[/red] Set a backend command under "
            "plugin.codewrite in .codegraph/config.toml "
            '(command = "claude -p").'
        )
        raise SystemExit(1)

    try:
        result = run_generation(
            root,
            args.spec,
            args.target,
            args.reference or None,
            config=config,
            backend=backend,
            force=args.force,
            to_stdout=args.stdout,
        )
    except (CodeWriteError, GenerationError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if args.stdout:
        # code to stdout stays clean; the note goes to stderr via rich stderr
        Console(stderr=True).print(
            f"[dim]{result['reference']} -> {result['lines']} lines "
            f"({result['backend']}, egress: {result['egress']})[/dim]"
        )
        print(result["code"])
        return

    console.print(
        f"[green]wrote[/green] {result['target']}  "
        f"[dim]({result['lines']} lines, mirror of {result['reference']})[/dim]"
    )
    console.print(
        f"[dim]backend: {result['backend']}  egress: {result['egress']}  "
        "verify by running the checks, not by trusting this output.[/dim]"
    )
