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
        p.add_argument("image", help="Path to the image file")
        p.add_argument(
            "--profile",
            default=None,
            choices=["default", "fast", "photo"],
            help="Pipeline profile (default from [plugin.vision])",
        )
        p.set_defaults(func=lambda args: _run(args, dict(config)))

    return register_cli


def _run(args, config: dict) -> None:
    from .backends import available
    from .pipeline import route

    if args.profile:
        config["profile"] = args.profile
    if not available(config):
        raise SystemExit(
            "vision backend: Ollama daemon not reachable at "
            + str(config.get("ollama_url", "http://127.0.0.1:11434"))
            + " (install: https://ollama.com, then `ollama pull qwen2.5vl:3b gemma3:4b`)"
        )
    _inv, markdown = route(Path(args.image), config)
    print(markdown)
