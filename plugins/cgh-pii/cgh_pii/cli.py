# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh pii redact <file>`: anonymize a text or markdown file
#              in place or into a new one, keeping only the PII
#              categories asked for (--only person, ...). person and
#              location need the NER extra. Binary documents (pdf, docx)
#              are not rewritten in place; the command says so and points
#              at extracting their text first.

from __future__ import annotations

import os
from pathlib import Path

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".log", ".json"}


def make_cli_registrar(config: dict):
    def register_cli(subparsers) -> None:
        p = subparsers.add_parser(
            "pii", help="Redact PII in a text file (anonymize names, emails, ...)"
        )
        p.add_argument("action", nargs="?", default="redact", choices=["redact"])
        p.add_argument("file", nargs="?", help="Text or markdown file to redact")
        p.add_argument(
            "--only",
            default="",
            help="Comma-separated categories: person,location,email,phone,"
            "iban,card,aws_key,private_key (default: all)",
        )
        p.add_argument(
            "--mode",
            default="placeholder",
            choices=["placeholder", "pseudonym"],
            help="placeholder [PERSON_1] (default) or keyed pseudonym",
        )
        p.add_argument("--out", default="", help="Write to this file instead of stdout")
        p.add_argument(
            "--in-place", action="store_true", help="Overwrite the input file"
        )
        p.set_defaults(func=lambda args: _dispatch(args, config))

    return register_cli


def _dispatch(args, config: dict) -> None:
    from rich.console import Console

    from .redact import RedactError, redact

    err = Console(stderr=True)
    if not args.file:
        err.print(
            "[red]usage: cgh pii redact <file> [--only person] [--out FILE][/red]"
        )
        raise SystemExit(2)
    src = Path(args.file)
    if not src.exists():
        err.print(f"[red]not found:[/red] {args.file}")
        raise SystemExit(2)
    if src.suffix.lower() in (".pdf", ".docx", ".xlsx"):
        err.print(
            f"[yellow]{src.suffix} is a binary document.[/yellow] cgh pii redact "
            "rewrites text files only; formatted in-place redaction of pdf/docx "
            "is not supported. Extract the text first, then redact that."
        )
        raise SystemExit(2)
    if src.suffix.lower() not in _TEXT_SUFFIXES:
        err.print(f"[dim]note: {src.suffix or 'no suffix'} treated as text.[/dim]")

    text = src.read_text(encoding="utf-8", errors="replace")
    only = [c.strip() for c in args.only.split(",") if c.strip()] or None
    key = os.environ.get("CGH_REDACT_SECRET")
    secret = key.encode("utf-8") if key and len(key) >= 16 else None
    try:
        out, counts = redact(text, only=only, mode=args.mode, secret=secret)
    except RedactError as exc:
        err.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    summary = ", ".join(f"{n} {c}" for c, n in sorted(counts.items())) or "nothing"
    if args.in_place:
        src.write_text(out, encoding="utf-8")
        err.print(f"[green]+[/green] redacted in place ({summary})")
    elif args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out, encoding="utf-8")
        err.print(f"[green]+[/green] wrote {target} ({summary})")
    else:
        print(out)
        err.print(f"[dim]redacted {summary}; --out FILE to save[/dim]")
