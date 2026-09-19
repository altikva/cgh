# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh artifact` verb: a read-through cache for files cgh cannot
#              parse into the graph (pdf, images, office docs). An agent that
#              inspects such a file records what it learned; the next session
#              recalls it instead of paying the read again. Stored as a
#              knowledge entry (kind "note", tag "artifact", file_refs the path)
#              with the file's SHA-256 in the body, so recall can tell a fresh
#              summary from one whose file has changed. `cgh artifact recall
#              <path>` reads, `cgh artifact note <path> --summary "..."` writes,
#              `cgh artifact list` browses. ARTIFACT_EXTS and the hash/marker
#              helpers are shared with the Read precheck hook.

from __future__ import annotations

import argparse
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from codegraph.cli import console

_TAG = "artifact"

# Extensions cgh does not parse into the graph: opaque binaries an agent has to
# open with vision or an external extractor, which is exactly the expensive read
# worth caching. Plain-text formats (.md, .csv, .json, source code) are left out
# on purpose, cgh already indexes or greps those cheaply.
ARTIFACT_EXTS = frozenset(
    {
        # images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".svg",
        ".ico",
        # documents
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".epub",
    }
)

# A machine-readable line appended to the note body so recall (and the hook)
# can compare the stored digest against the file on disk. Kept on its own line
# and stripped before the human-facing summary is shown.
_MARKER_RE = re.compile(r"cgh-artifact:\s*sha256=([0-9a-fA-F]{64})")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file, so a large PDF or image is not read into
    memory whole."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def make_marker(sha: str, size: int) -> str:
    return f"cgh-artifact: sha256={sha} bytes={size}"


def parse_stored_sha(body: str) -> str | None:
    m = _MARKER_RE.search(body or "")
    return m.group(1).lower() if m else None


def strip_marker(body: str) -> str:
    """The human-facing summary, without the machine marker line."""
    kept = [
        ln
        for ln in (body or "").splitlines()
        if not ln.strip().lower().startswith("cgh-artifact:")
    ]
    return "\n".join(kept).strip()


def _relref(path: Path, root: Path) -> str:
    """The path as cgh stores it: repo-relative when inside the root, absolute
    otherwise. Recall and the hook compute the same ref to match."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def register_artifact_parser(sub) -> None:
    p = sub.add_parser(
        "artifact",
        help="Recall/record summaries of files cgh can't parse (pdf, images, office docs)",
    )
    asub = p.add_subparsers(dest="artifact_cmd")

    n = asub.add_parser("note", help="Record what inspecting a file revealed")
    n.add_argument("path", help="The file that was inspected")
    n.add_argument(
        "--summary", default="", help="What you learned (the point of the file)"
    )
    n.add_argument("--root", default=os.getcwd(), help="Repo root (default: cwd)")

    r = asub.add_parser(
        "recall", help="Show cgh's saved summary for a file, and whether it is fresh"
    )
    r.add_argument("path", help="The file to recall")
    r.add_argument("--root", default=os.getcwd(), help="Repo root (default: cwd)")

    ls = asub.add_parser("list", help="List saved artifact summaries in this repo")
    ls.add_argument("--limit", type=int, default=30, help="Max results to show")
    ls.add_argument("--root", default=os.getcwd(), help="Repo root (default: cwd)")


def cmd_artifact(args: argparse.Namespace) -> None:
    cmd = getattr(args, "artifact_cmd", None) or "list"
    root = Path(os.path.abspath(getattr(args, "root", None) or os.getcwd()))
    if cmd == "note":
        _do_note(args, root)
    elif cmd == "recall":
        _do_recall(args, root)
    else:
        _do_list(args, root)


def _do_note(args: argparse.Namespace, root: Path) -> None:
    from codegraph.state.call_log import knowledge_record

    path = Path(os.path.abspath(args.path))
    if not path.is_file():
        console.print(f"[red]No such file:[/red] {args.path}")
        raise SystemExit(1)
    summary = (args.summary or "").strip()
    if not summary:
        console.print(
            '[red]Usage:[/red] cgh artifact note <path> --summary "<what you learned>"'
        )
        raise SystemExit(1)

    ref = _relref(path, root)
    size = path.stat().st_size
    sha = sha256_file(path)
    body = f"{summary}\n\n{make_marker(sha, size)}"
    row_id = knowledge_record(
        title=path.name,
        body=body,
        kind="note",
        tags=_TAG,
        file_refs=[ref],
        repo_root=root,
    )
    console.print(
        f"[green]Artifact summary saved[/green] (knowledge #{row_id}) for "
        f"[cyan]{ref}[/cyan]. The next read recalls it instead of re-inspecting."
    )


def _find_note(ref: str, path: Path, root: Path) -> dict | None:
    from codegraph.state.call_log import knowledge_list

    rows = knowledge_list(kind="note", tag=_TAG, repo_root=root, limit=500)
    abs_ref = str(path.resolve())
    for r in rows:  # newest first
        refs = r.get("file_refs") or []
        if ref in refs or abs_ref in refs:
            return r
    return None


def _freshness(path: Path, stored: str | None) -> str:
    if not path.is_file():
        return "missing"
    if not stored:
        return "unverified"
    try:
        return "fresh" if sha256_file(path) == stored else "stale"
    except OSError:
        return "unverified"


def _do_recall(args: argparse.Namespace, root: Path) -> None:
    path = Path(os.path.abspath(args.path))
    ref = _relref(path, root)
    hit = _find_note(ref, path, root)
    if hit is None:
        console.print(
            f"[yellow]No saved summary[/yellow] for '{ref}'. "
            f'Inspect it, then: cgh artifact note {args.path} --summary "..."'
        )
        return

    body = hit.get("body") or ""
    summary = strip_marker(body)
    state = _freshness(path, parse_stored_sha(body))
    badge = {
        "fresh": "[green]fresh (file unchanged since the summary)[/green]",
        "stale": "[red]STALE, the file changed since this summary was saved[/red]",
        "missing": "[yellow]file not found on disk now[/yellow]",
        "unverified": "[dim]freshness unverified[/dim]",
    }[state]
    console.print(f"[bold]{ref}[/bold]  {badge}")
    console.print(summary or "[dim](no summary text)[/dim]")
    if state == "stale":
        console.print(
            "[dim]Re-inspect and save a new summary; this one describes an older version.[/dim]"
        )


def _do_list(args: argparse.Namespace, root: Path) -> None:
    from codegraph.state.call_log import knowledge_list

    rows = knowledge_list(kind="note", tag=_TAG, repo_root=root, limit=args.limit)
    if not rows:
        console.print(
            "[dim]No artifact summaries yet. After inspecting a pdf/image/doc, save one:[/dim] "
            'cgh artifact note <path> --summary "..."'
        )
        return

    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("date", style="dim", no_wrap=True)
    table.add_column("file")
    table.add_column("summary", overflow="fold")
    for r in rows:
        try:
            date = datetime.fromtimestamp(float(r.get("ts") or 0)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            date = "?"
        refs = r.get("file_refs") or []
        ref = refs[0] if refs else (r.get("title") or "")
        first = strip_marker(r.get("body") or "").splitlines()
        table.add_row(date, ref, first[0] if first else "")
    console.print(f"[bold]artifact summaries[/bold]  [dim]({len(rows)})[/dim]")
    console.print(table)
    console.print(
        "[dim]cgh surfaces these automatically before you re-read a file. "
        "Recall one: cgh artifact recall <path>.[/dim]"
    )
