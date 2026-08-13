# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the deferred vision
#              scanner and the `cgh vision` CLI verb. Also the module
#              codegraph.sdk reaches for its image_* functions, so the
#              pipeline entry points are re-exported here: inventory,
#              extract_diagram, extract_tables, extract_charts, route.

from __future__ import annotations

from pathlib import Path

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .cli import make_cli_registrar
    from .image_parser import IMAGE_EXTENSIONS, ImageParser
    from .scanner import VisionScanner

    # Claim image extensions so images are indexed at all; the indexer skips
    # any file no parser claims, which is why images were invisible until
    # now (and the deferred vision scanner, which only runs on indexed
    # files, never fired).
    api.register_parser(*IMAGE_EXTENSIONS)(ImageParser)
    api.register_scanner(VisionScanner(api.config, api.repo_root))
    api.register_cli(make_cli_registrar(api.config))


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
