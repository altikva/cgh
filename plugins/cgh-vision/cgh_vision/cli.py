# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh vision <image> [--profile default|fast|photo]`:
#              runs the routed pipeline on one image and prints the
#              markdown (with its Mermaid block) on stdout. The banner
#              rides the cgh dispatcher like every other verb.

from __future__ import annotations

from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def make_cli_registrar(config: dict):
    def register_cli(subparsers) -> None:
        p = subparsers.add_parser(
            "vision",
            help="Inventory and extract an image or pdf (markdown + Mermaid)",
        )
        p.add_argument(
            "image",
            nargs="?",
            help="Path to an image or pdf, or 'setup' to configure a backend",
        )
        p.add_argument(
            "--profile",
            default=None,
            choices=["default", "fast", "photo"],
            help="Pipeline profile (default from [plugin.vision])",
        )
        p.add_argument(
            "--llamacpp",
            action="store_true",
            help="With 'setup': use a local llama.cpp server instead of Ollama",
        )
        p.add_argument(
            "--hint",
            default=None,
            help="Steer extraction, e.g. 'labels are in French' "
            "(appended to the prompt, never replaces the JSON contract)",
        )
        p.add_argument(
            "--pages",
            default="",
            help="For a PDF: which pages to read (e.g. '1-3', '2,5', "
            "default all). Ignored for an image.",
        )
        p.add_argument(
            "--force",
            action="store_true",
            help="Recompute even if a cached result exists for this file "
            "(and refresh the cache)",
        )
        from codegraph.plugin_api import add_format_option, add_out_option

        add_out_option(p, what="the report")
        add_format_option(p)  # md (human) or json (the SDK dicts)
        p.set_defaults(func=lambda args: _run(args, dict(config)))

    return register_cli


def _ensure_models(config: dict) -> None:
    """Before the extraction bar starts, name any missing models and, on
    the Ollama backend, fetch them from Hugging Face (with ollama's own
    download progress) unless vision_auto_fetch is off. Runs pre-flight so
    ollama's progress bar does not fight the extraction spinner, and points
    at the manual route when auto-fetch cannot help."""
    from rich.console import Console

    from .backends import backend_kind, fetch_model_from_hf, missing_models
    from .pipeline import profile_for

    err = Console(stderr=True)
    profile = profile_for(config)
    wanted = [
        profile.get("nodes_model"),
        profile.get("edges_model"),
        profile.get("fallback_model"),
    ]
    with err.status("[dim]checking models...", spinner="dots"):
        missing = missing_models(config, [str(m) for m in wanted if m])
    if not missing:
        return
    err.print(f"[yellow]missing model(s):[/yellow] {', '.join(missing)}")
    if backend_kind(config) == "openai":
        # An OpenAI-compatible endpoint loads its own models; naming
        # the wrong one is a config issue, not a pull.
        err.print(
            "[dim]  the configured endpoint does not serve these; set "
            "nodes_model / edges_model to a model it exposes.[/dim]"
        )
        return
    for m in missing:
        # Shows ollama's native download bar. Returns False (no-op) when
        # auto-fetch is off, the model is unmapped, or ollama is absent,
        # in which case we print the manual route.
        if not fetch_model_from_hf(m, config):
            err.print(f"[dim]  ollama pull {m}[/dim]")
            err.print(
                "[dim]  blocked by your network? register a GGUF locally "
                "instead, see the 'When ollama pull is blocked' section of "
                "the cgh-vision README (weights + mmproj, then ollama "
                "create).[/dim]"
            )


def _run(args, config: dict) -> None:
    from .backends import available

    if args.image == "setup":
        if not getattr(args, "llamacpp", False):
            raise SystemExit(
                "usage: cgh vision setup --llamacpp   "
                "(configure a local llama.cpp server, no Ollama)"
            )
        from .setup_llamacpp import setup_llamacpp

        setup_llamacpp(config)
        return
    if not args.image:
        raise SystemExit("usage: cgh vision <image>   |   cgh vision setup --llamacpp")

    if args.profile:
        config["profile"] = args.profile
    if getattr(args, "hint", None):
        config["hint"] = args.hint

    # Validate the input BEFORE touching the backend, so a wrong file type
    # fails fast with a clear message instead of a cryptic Pillow error.
    from rich.console import Console

    err = Console(stderr=True)
    src = Path(args.image)
    if not src.exists():
        err.print(f"[red]not found:[/red] {args.image}")
        raise SystemExit(2)
    suffix = src.suffix.lower()
    if suffix != ".pdf" and suffix not in _IMAGE_EXTS:
        err.print(
            f"[red]cgh vision reads an image[/red] ({', '.join(sorted(_IMAGE_EXTS))}) "
            "or a pdf. "
            + (
                "Extract a document's text with cgh-docs instead."
                if suffix in (".docx", ".xlsx", ".txt", ".md")
                else "Convert this file to an image first."
            )
        )
        raise SystemExit(2)

    if not available(config):
        from .setup_ollama import offer_to_install, print_install_help

        url = str(config.get("ollama_url", "http://127.0.0.1:11434"))
        print_install_help(err, ollama_url=url)
        offer_to_install(err)  # interactive only; official channel only
        raise SystemExit(1)

    if suffix == ".pdf":
        _run_pdf(args, config, src)
        return

    _ensure_models(config)
    from .backends import VisionError

    try:
        result = _extract_one(src, config, force=getattr(args, "force", False))
    except VisionError as exc:
        # A missing model, an unreachable daemon, a bad response: a clear
        # message on stderr and a non-zero exit, never a crash report.
        err.print(f"[red]vision failed:[/red] {exc}")
        raise SystemExit(1) from exc
    _emit(args, result)


def _extract_one(image: Path, config: dict, force: bool = False) -> dict:
    """Run the vision pipeline on one image with a stderr progress bar
    (stdout stays pure markdown / json, so it pipes). Vision inference is
    slow, so the result is cached by the image bytes plus the parameters
    that shape it; a re-run of the same image (say, later with --out)
    returns the cached answer instantly. --force recomputes and refreshes
    the cache."""
    from rich.console import Console

    from . import cache

    err = Console(stderr=True)
    img_bytes = Path(image).read_bytes()
    if not force:
        hit = cache.get(config, img_bytes)
        if hit is not None:
            err.print("[dim]cached result (use --force to recompute)[/dim]")
            return hit

    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .pipeline import route_structured

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=Console(stderr=True),
        transient=True,
    ) as bar:
        task = bar.add_task("warming up", total=None)
        result = route_structured(
            image, config, progress=lambda step: bar.update(task, description=step)
        )
    cache.put(config, img_bytes, result)
    return result


def _run_pdf(args, config: dict, src: Path) -> None:
    """Rasterize the requested PDF pages to images and run vision on each,
    then emit a per-page report. The heavy dependency (pypdfium2) is only
    needed here, behind the [pdf] extra."""
    from rich.console import Console

    from .backends import VisionError
    from .pdf_render import PdfRenderError, iter_pdf_pages

    err = Console(stderr=True)
    _ensure_models(config)
    per_page: list[dict] = []
    try:
        for page_no, png in iter_pdf_pages(src, pages=getattr(args, "pages", "")):
            try:
                err.print(f"[dim]page {page_no}...[/dim]")
                result = _extract_one(png, config, force=getattr(args, "force", False))
                result["page"] = page_no
                per_page.append(result)
            finally:
                png.unlink(missing_ok=True)
    except PdfRenderError as exc:
        err.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except VisionError as exc:
        err.print(f"[red]vision failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if not per_page:
        err.print("[yellow]no pages read from the pdf.[/yellow]")
        raise SystemExit(1)
    _emit_pdf(args, src, per_page)


def _emit(args, result: dict) -> None:
    from codegraph.plugin_api import emit_result

    from .pipeline import render_markdown

    if getattr(args, "format", "md") == "json":
        import json

        emit_result(
            json.dumps(_json_payload(result), indent=2),
            out=args.out,
            hint="report.json",
        )
    else:
        emit_result(render_markdown(result), out=args.out, hint="report.md")


def _emit_pdf(args, src: Path, per_page: list[dict]) -> None:
    from codegraph.plugin_api import emit_result

    from .pipeline import render_markdown

    if getattr(args, "format", "md") == "json":
        import json

        payload = {
            "file": src.name,
            "pages": [{"page": r.get("page"), **_json_payload(r)} for r in per_page],
        }
        emit_result(json.dumps(payload, indent=2), out=args.out, hint="report.json")
    else:
        parts = [f"# {src.name}", ""]
        for r in per_page:
            parts.append(f"## Page {r.get('page')}")
            parts.append(render_markdown(r))
            parts.append("")
        emit_result("\n".join(parts), out=args.out, hint="report.md")


def _json_payload(result: dict) -> dict:
    payload = {k: v for k, v in result.items() if k != "page"}
    if payload.get("diagram") is not None:
        # The markdown projection is the other format; mermaid stays.
        payload["diagram"] = {
            k: v for k, v in payload["diagram"].items() if k != "markdown"
        }
    return payload
