# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh classify` verbs. label / unlabel maintain the ground
#              truth and refresh the file's finding on the spot; train
#              fits the model on labeled files and sweeps the repo;
#              review lists the files the model is unsure about; status
#              shows label counts and model state.

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def make_cli_registrar(config: dict):
    def add_cli(sub) -> None:
        p = sub.add_parser(
            "classify", help="Label files as confidential, train the local classifier"
        )
        p.add_argument(
            "action",
            nargs="?",
            default="status",
            choices=["status", "label", "unlabel", "train", "review"],
        )
        p.add_argument("files", nargs="*", help="Files for label/unlabel")
        p.add_argument(
            "--not",
            dest="not_confidential",
            action="store_true",
            help="Label as public instead of confidential",
        )
        p.add_argument("--root", default=os.getcwd())
        p.set_defaults(func=lambda args: _dispatch(args, config))

    return add_cli


def _dispatch(args, config: dict) -> None:
    from rich.console import Console

    console = Console()
    root = Path(os.path.abspath(args.root))
    action = args.action

    if action in ("label", "unlabel") and not args.files:
        console.print(f"[red]Usage: cgh classify {action} <file> [<file> ...][/red]")
        return
    if action == "label":
        _cmd_label(console, root, args.files, not args.not_confidential, config)
    elif action == "unlabel":
        _cmd_unlabel(console, root, args.files)
    elif action == "train":
        _cmd_train(console, root, config)
    elif action == "review":
        _cmd_review(console, root)
    else:
        _cmd_status(console, root)


def _refresh_finding(root: Path, path: Path, config: dict) -> None:
    """Re-run the classify scanner on one file so the finding matches the
    labels right now, not at the next reindex."""
    from codegraph.plugin_api import record_findings

    from .scanner import ClassifyScanner

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    scanner = ClassifyScanner(config, root)
    record_findings(root, str(path), scanner.name, scanner.scan(path, text, None))


def _cmd_label(console, root: Path, files: list[str], confidential: bool, config: dict):
    from .labels import set_label

    for raw in files:
        path = Path(raw) if os.path.isabs(raw) else root / raw
        if not path.exists():
            console.print(f"[red]not found:[/red] {raw}")
            continue
        set_label(root, path, confidential)
        _refresh_finding(root, path.resolve(), config)
        badge = "[red]confidential[/red]" if confidential else "[green]public[/green]"
        console.print(f"  {badge}  {raw}")
    console.print(
        "[dim]Retrain with[/dim] [cyan]cgh classify train[/cyan] [dim]to propagate.[/dim]"
    )


def _cmd_unlabel(console, root: Path, files: list[str]):
    from .labels import remove_label

    for raw in files:
        path = Path(raw) if os.path.isabs(raw) else root / raw
        if remove_label(root, path):
            console.print(f"  [dim]unlabeled[/dim]  {raw}")
        else:
            console.print(f"  [dim]was not labeled:[/dim] {raw}")


def _cmd_train(console, root: Path, config: dict):
    from codegraph.plugin_api import record_findings

    from .labels import load_labels
    from .model import NaiveBayesModel, model_path
    from .scanner import ClassifyScanner

    labels = load_labels(root)
    docs: list[tuple[str, bool]] = []
    for path_str, confidential in labels.items():
        try:
            docs.append(
                (
                    Path(path_str).read_text(encoding="utf-8", errors="replace"),
                    confidential,
                )
            )
        except OSError:
            continue
    try:
        model = NaiveBayesModel.train(docs)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        console.print(
            "[dim]Label at least one confidential and one public file first.[/dim]"
        )
        return
    model.save(model_path(root))
    console.print(
        f"[green]+[/green] model trained on {model.trained_on} labeled file(s)."
    )

    # Sweep: classify every tracked, parser-supported file now.
    scanner = ClassifyScanner(config, root)
    swept = flagged = 0
    for path in _tracked_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = scanner.scan(path, text, None)
        record_findings(root, str(path), scanner.name, found)
        swept += 1
        if any(f.key == "confidential" and f.value == "true" for f in found):
            flagged += 1
    console.print(
        f"[green]+[/green] swept {swept} file(s), {flagged} flagged confidential."
    )

    # Secure mode: mirror the fresh flags into Claude Code's static deny
    # rules right away (older cgh without the guard: silently skip).
    try:
        from codegraph.plugin_api import sync_static_rules

        added, removed = sync_static_rules(root)
        if added or removed:
            console.print(
                f"[green]+[/green] guard deny rules synced "
                f"({added} added, {removed} removed)."
            )
    except ImportError:
        pass


def _cmd_review(console, root: Path):
    from codegraph.plugin_api import query_findings

    rows = query_findings(root, key_prefix="confidential.uncertain", limit=100)
    if not rows:
        console.print(
            "[dim]Nothing uncertain. Train first, or the model is confident.[/dim]"
        )
        return
    console.print("[bold]Files the model is unsure about:[/bold]")
    for row in rows:
        console.print(f"  p={row['value']}  {row['file']}")
    console.print(
        "[dim]Decide with[/dim] [cyan]cgh classify label <file> [--not][/cyan]"
    )


def _cmd_status(console, root: Path):
    from .labels import load_labels
    from .model import NaiveBayesModel, model_path

    labels = load_labels(root)
    confidential = sum(1 for v in labels.values() if v)
    console.print(
        f"[bold]labels:[/bold] {len(labels)} "
        f"({confidential} confidential, {len(labels) - confidential} public)"
    )
    model = NaiveBayesModel.load(model_path(root))
    if model is None:
        console.print("[bold]model:[/bold] [dim]not trained yet[/dim]")
    else:
        console.print(f"[bold]model:[/bold] trained on {model.trained_on} file(s)")


def _tracked_files(root: Path) -> list[Path]:
    from codegraph.plugin_api import is_supported

    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [root / line for line in out.splitlines() if line and is_supported(line)]
