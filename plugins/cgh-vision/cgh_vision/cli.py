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


def make_cli_registrar(config: dict):
    def register_cli(subparsers) -> None:
        p = subparsers.add_parser(
            "vision", help="Inventory and extract one image (markdown + Mermaid)"
        )
        p.add_argument(
            "image",
            nargs="?",
            help="Path to the image file, or 'setup' to configure a backend",
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
        from codegraph.plugin_api import add_format_option, add_out_option

        add_out_option(p, what="the report")
        add_format_option(p)  # md (human) or json (the SDK dicts)
        p.set_defaults(func=lambda args: _run(args, dict(config)))

    return register_cli


def _warn_missing_models(config: dict) -> None:
    """Name the models the daemon lacks before spending a minute
    failing on them, and point at the route that works when
    `ollama pull` is blocked by a corporate network."""
    from rich.console import Console

    from .backends import backend_kind, missing_models
    from .pipeline import profile_for

    profile = profile_for(config)
    wanted = [profile.get("nodes_model"), profile.get("edges_model")]
    missing = missing_models(config, [str(m) for m in wanted if m])
    if not missing:
        return
    err = Console(stderr=True)
    err.print(f"[yellow]missing model(s):[/yellow] {', '.join(missing)}")
    if backend_kind(config) == "openai":
        # An OpenAI-compatible endpoint loads its own models; naming
        # the wrong one is a config issue, not a pull.
        err.print(
            "[dim]  the configured endpoint does not serve these; set "
            "nodes_model / edges_model to a model it exposes.[/dim]"
        )
        return
    err.print(f"[dim]  ollama pull {' '.join(missing)}[/dim]")
    err.print(
        "[dim]  blocked by your network? register a GGUF locally instead, "
        "see the 'When ollama pull is blocked' section of the cgh-vision "
        "README (weights + mmproj projector, then ollama create).[/dim]"
    )


def _run(args, config: dict) -> None:
    from .backends import available
    from .pipeline import render_markdown, route_structured

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
    if not available(config):
        from rich.console import Console

        from .setup_ollama import offer_to_install, print_install_help

        err = Console(stderr=True)
        url = str(config.get("ollama_url", "http://127.0.0.1:11434"))
        print_install_help(err, ollama_url=url)
        offer_to_install(err)  # interactive only; official channel only
        raise SystemExit(1)
    _warn_missing_models(config)
    # Progress rides stderr so stdout stays pure markdown (pipeable).
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .backends import VisionError

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=Console(stderr=True),
            transient=True,
        ) as bar:
            task = bar.add_task("warming up", total=None)
            result = route_structured(
                Path(args.image),
                config,
                progress=lambda step: bar.update(task, description=step),
            )
    except VisionError as exc:
        # A missing model, an unreachable daemon, a bad response: a clear
        # message on stderr and a non-zero exit, never a crash report.
        Console(stderr=True).print(f"[red]vision failed:[/red] {exc}")
        raise SystemExit(1) from exc
    from codegraph.plugin_api import emit_result

    if getattr(args, "format", "md") == "json":
        import json

        payload = dict(result)
        if payload["diagram"] is not None:
            # The markdown projection is the other format; mermaid stays.
            payload["diagram"] = {
                k: v for k, v in payload["diagram"].items() if k != "markdown"
            }
        emit_result(json.dumps(payload, indent=2), out=args.out, hint="report.json")
    else:
        emit_result(render_markdown(result), out=args.out, hint="report.md")
