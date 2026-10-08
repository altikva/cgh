# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh pii` verbs. scan [PATH...]: on-demand secret scan
#              (PII patterns with --pii), exit 1 on a block-severity hit so
#              it can gate CI. redact <file>: anonymize a text, markdown or
#              docx file, keeping only the categories asked for (--only
#              person, ...); person and location need the NER extra.
#              probe <file>: show what the LLM tier would flag.

from __future__ import annotations

import os
from pathlib import Path

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".log", ".json"}


def make_cli_registrar(config: dict, repo_root=None):
    def register_cli(subparsers) -> None:
        p = subparsers.add_parser(
            "pii",
            help="Scan for secrets (cgh pii scan), or redact PII in a file",
        )
        p.add_argument(
            "action",
            nargs="?",
            default="redact",
            choices=["scan", "redact", "probe"],
        )
        p.add_argument(
            "paths",
            nargs="*",
            metavar="PATH",
            help="scan: files or directories (default: the current directory); "
            "redact / probe: the one file to process",
        )
        p.add_argument(
            "--pii",
            action="store_true",
            help="scan: also report PII patterns (emails, phones, IBANs, "
            "cards). Noisy on code; secrets only by default.",
        )
        p.add_argument("--json", action="store_true", help="scan: print hits as JSON")
        p.add_argument(
            "--only",
            default="",
            help="Comma-separated categories: person,location,email,phone,"
            "iban,card,aws_key,private_key,other (default: all)",
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
        p.add_argument(
            "--llm",
            action="store_true",
            help="Also probe with the configured LLM (catches PII regex/NER "
            "miss). A non-loopback endpoint needs pii_llm_allow_remote; "
            "every probe is audited.",
        )
        p.set_defaults(func=lambda args: _dispatch(args, config, repo_root))

    return register_cli


def _dispatch(args, config: dict, repo_root=None) -> None:
    from rich.console import Console

    from .redact import RedactError, redact

    err = Console(stderr=True)
    root = repo_root if repo_root is not None else Path.cwd()
    if args.action == "scan":
        _scan(err, args, config)
        return
    if len(args.paths) > 1:
        err.print(f"[red]{args.action} takes one file, got {len(args.paths)}[/red]")
        raise SystemExit(2)
    args.file = args.paths[0] if args.paths else ""
    if not args.file:
        err.print(
            "[red]usage: cgh pii scan [PATH...] [--pii], "
            "cgh pii redact <file> [--only person] [--llm] [--out FILE], "
            "or cgh pii probe <file>[/red]"
        )
        raise SystemExit(2)
    src = Path(args.file)
    if not src.exists():
        err.print(f"[red]not found:[/red] {args.file}")
        raise SystemExit(2)

    if args.action == "probe":
        _probe(err, src, config, root)
        return

    only = [c.strip() for c in args.only.split(",") if c.strip()] or None
    key = os.environ.get("CGH_REDACT_SECRET")
    secret = key.encode("utf-8") if key and len(key) >= 16 else None

    if src.suffix.lower() == ".docx":
        if args.llm:
            err.print(
                "[yellow]--llm is not wired for docx yet; redacting it with "
                "the regex and NER tiers only.[/yellow]"
            )
        _redact_docx(err, args, src, only, secret)
        return
    if src.suffix.lower() in (".pdf", ".xlsx"):
        err.print(
            f"[yellow]{src.suffix} is not supported.[/yellow] cgh pii redact "
            "handles text, markdown and docx. Extract the text of a pdf first "
            "(see cgh-docs), then redact that."
        )
        raise SystemExit(2)
    if src.suffix.lower() not in _TEXT_SUFFIXES:
        err.print(f"[dim]note: {src.suffix or 'no suffix'} treated as text.[/dim]")

    text = src.read_text(encoding="utf-8", errors="replace")
    llm_hits = _llm_hits(err, text, config, root, src) if args.llm else None
    try:
        out, counts = redact(
            text, only=only, mode=args.mode, secret=secret, llm_hits=llm_hits
        )
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


def _scan(err, args, config: dict) -> None:
    """`cgh pii scan [PATH...]`: print one line per (file, key) and exit 1
    when any block-severity secret is found, 0 otherwise."""
    import json

    from . import as_bool
    from .scan import scan_paths

    paths = args.paths or ["."]
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        err.print(f"[red]not found:[/red] {', '.join(missing)}")
        raise SystemExit(2)
    pii = args.pii or as_bool(config.get("pii", False))
    hits = scan_paths(paths, pii=pii, disabled_keys=set(config.get("disable_keys", [])))
    blocking = sum(1 for h in hits if h.severity == "block")
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], indent=2))
    else:
        for h in hits:
            print(f"{h.path}:{h.line}\t{h.severity}\t{h.key}\t({h.count})")
        files = len({h.path for h in hits})
        what = "secrets and PII" if pii else "secrets"
        if hits:
            err.print(
                f"[bold]{len(hits)}[/bold] hit(s) in {files} file(s), "
                f"{blocking} blocking ({what})"
            )
        else:
            err.print(f"[green]no {what} found[/green]")
    if blocking:
        raise SystemExit(1)


def _summary(counts: dict) -> str:
    return ", ".join(f"{n} {c}" for c, n in sorted(counts.items())) or "nothing"


def _llm_hits(err, text: str, config: dict, root, src):
    """(redaction_category, quote) pairs from the LLM tier, or None. A
    denied egress gate prints why and returns None so redaction proceeds
    with the regex and NER tiers only, never silently."""
    from . import llm

    try:
        raw = llm.probe(text, config, root, str(src))
    except llm.LlmProbeError as exc:
        err.print(f"[yellow]LLM tier skipped:[/yellow] {exc}")
        return None
    if not raw:
        err.print("[dim]LLM tier: no additional PII found (or backend down).[/dim]")
        return None
    return [(llm.redaction_category(cat), quote) for cat, quote in raw]


def _probe(err, src: Path, config: dict, root) -> None:
    """`cgh pii probe <file>`: run only the LLM tier and print what it
    finds, without redacting. Handy to see whether the LLM adds anything
    over regex + NER before committing to a redaction."""
    from . import llm

    text = src.read_text(encoding="utf-8", errors="replace")
    try:
        hits = llm.probe(text, config, root, str(src))
    except llm.LlmProbeError as exc:
        err.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    if not hits:
        err.print("[dim]no PII found by the LLM tier (or backend unreachable).[/dim]")
        return
    by_cat: dict[str, int] = {}
    for cat, _quote in hits:
        by_cat[cat] = by_cat.get(cat, 0) + 1
    summary = ", ".join(f"{n} {c}" for c, n in sorted(by_cat.items()))
    err.print(f"[green]{len(hits)} hit(s):[/green] {summary}")
    err.print("[dim]run `cgh pii redact <file> --llm` to anonymize them.[/dim]")


def _redact_docx(err, args, src, only, secret) -> None:
    """docx path (the [docx] extra). Needs an --out or --in-place: a
    docx cannot go to stdout. Formatting inside changed paragraphs is
    flattened; unchanged paragraphs keep it."""
    from .redact import RedactError

    if not (args.out or args.in_place):
        err.print(
            "[red]docx needs --out FILE or --in-place (cannot go to stdout).[/red]"
        )
        raise SystemExit(2)
    try:
        from .redact_docx import redact_docx_file
    except ImportError as exc:
        err.print(
            '[red]docx redaction needs the extra: pip install "cgh-pii[docx]"[/red]'
        )
        raise SystemExit(1) from exc
    dst = src if args.in_place else Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        counts = redact_docx_file(src, dst, only=only, mode=args.mode, secret=secret)
    except RedactError as exc:
        err.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    where = "in place" if args.in_place else str(dst)
    err.print(
        f"[green]+[/green] redacted docx {where} ({_summary(counts)}); "
        "[dim]formatting inside changed paragraphs is flattened[/dim]"
    )
