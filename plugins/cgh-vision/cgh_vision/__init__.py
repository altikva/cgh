# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the `cgh vision` CLI
#              verb, and, only with [plugin.vision] scan_on_index = true,
#              the image parser and the deferred vision scanner that read
#              every indexed image through a local model. Also the module
#              codegraph.sdk reaches for its image_* functions, so the
#              pipeline entry points are re-exported here: inventory,
#              extract_diagram, extract_tables, extract_charts, route.

from __future__ import annotations

from pathlib import Path

CGH_PLUGIN_API = 1


def _scan_on_index(config: dict) -> bool:
    value = config.get("scan_on_index", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config))

    # Reading every image on every index cost minutes of local model time
    # for findings agents rarely read; `cgh vision <file>` does the same
    # work on demand. scan_on_index = true restores the index-time path.
    if not _scan_on_index(api.config):
        return

    from .image_parser import IMAGE_EXTENSIONS, ImageParser
    from .scanner import VisionScanner

    # Claim image extensions so images are indexed at all; the indexer
    # skips any file no parser claims, and the deferred vision scanner
    # only runs on indexed files.
    api.register_parser(*IMAGE_EXTENSIONS)(ImageParser)
    api.register_scanner(VisionScanner(api.config, api.repo_root))


# -- SDK surface (codegraph.sdk.image_*) ------------------------------------


def inventory(path: Path, config: dict) -> dict:
    from .pipeline import inventory as _inventory

    return _inventory(Path(path), config)


def extract_diagram(path: Path, config: dict) -> dict:
    from .pipeline import extract_diagram as _extract

    return _extract(Path(path), config)


def extract_tables(path: Path, config: dict) -> list[dict]:
    from .pipeline import extract_tables as _extract

    return _extract(Path(path), config)


def extract_charts(path: Path, config: dict) -> list[dict]:
    from .pipeline import extract_charts as _extract

    return _extract(Path(path), config)


def route(path: Path, config: dict) -> tuple[dict, str]:
    from .pipeline import route as _route

    return _route(Path(path), config)
