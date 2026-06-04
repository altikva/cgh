# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Parser plugin registry.
#
# Adding a new language to codegraph:
#   1. Create a file in codegraph/parsers/ (e.g., rust.py)
#   2. Subclass BaseParser
#   3. Decorate with @register_parser
#   4. Done: codegraph auto-discovers it on import
#
# Example:
#   @register_parser(".rs")
#   class RustParser(BaseParser):
#       lang = "rust"
#       extensions = [".rs"]
#       tree_sitter_lang = "rust"      # optional: auto-installs grammar
#       extracts = ["functions", "structs", "traits", "impls"]
#
#       def parse(self, path: Path) -> FileIndex:
#           ...

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseParser
    from .base import FileIndex as FileIndex

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseParser]] = {}  # extension -> parser class
_INSTANCES: dict[str, BaseParser] = {}  # extension -> parser instance (cached)


def register_parser(*extensions: str):
    """
    Class decorator that registers a parser for the given file extensions.

    Usage:
        @register_parser(".py", ".pyw")
        class PythonParser(BaseParser):
            ...
    """

    def decorator(cls):
        for ext in extensions:
            ext_lower = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            _REGISTRY[ext_lower] = cls
        return cls

    return decorator


def get_parser(extension: str) -> BaseParser | None:
    """Get a parser instance for a file extension. Returns None if unsupported."""
    ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    if ext not in _REGISTRY:
        return None
    if ext not in _INSTANCES:
        _INSTANCES[ext] = _REGISTRY[ext]()
    return _INSTANCES[ext]


def get_supported_extensions() -> list[str]:
    """Return all registered file extensions."""
    return sorted(_REGISTRY.keys())


def get_parser_info() -> list[dict]:
    """Return metadata about all registered parsers."""
    seen: dict[str, dict] = {}
    for ext, cls in _REGISTRY.items():
        name = cls.__name__
        if name not in seen:
            seen[name] = {
                "name": name,
                "lang": getattr(cls, "lang", "unknown"),
                "extensions": [],
                "extracts": getattr(cls, "extracts", []),
                "description": (cls.__doc__ or "").strip().split("\n")[0],
            }
        seen[name]["extensions"].append(ext)
    return list(seen.values())


# Files matched by name (no extension or ambiguous extension).
_NAME_REGISTRY: dict[str, str] = {}  # lowercase filename -> extension key in _REGISTRY


def register_by_name(filenames: list[str], ext_key: str):
    """Map specific filenames to a parser extension key (for Dockerfile etc.)."""
    for name in filenames:
        _NAME_REGISTRY[name.lower()] = ext_key


def is_supported(path: str | Path) -> bool:
    """Check if a file can be parsed (by extension or by filename)."""
    p = Path(path)
    if p.suffix.lower() in _REGISTRY:
        return True
    return p.name.lower() in _NAME_REGISTRY


def get_parser_for_path(path: str | Path) -> BaseParser | None:
    """Resolve parser by extension first, then by filename."""
    p = Path(path)
    parser = get_parser(p.suffix)
    if parser:
        return parser
    ext_key = _NAME_REGISTRY.get(p.name.lower())
    if ext_key:
        return get_parser(ext_key)
    return None


# ---------------------------------------------------------------------------
# Auto-discovery: import all modules in this package to trigger @register_parser
# ---------------------------------------------------------------------------


def _discover_parsers():
    """Import all parser modules in this package."""
    package_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        if module_name == "base":
            continue
        importlib.import_module(f".{module_name}", package=__package__)


_discover_parsers()

# Register well-known filenames that lack a unique extension
register_by_name(
    ["Dockerfile", "Dockerfile.dev", "Dockerfile.prod", "Dockerfile.staging"],
    ".dockerfile",
)
register_by_name(
    ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"],
    ".yaml",
)
register_by_name([".env.example", ".env.local", ".env.staging", ".env.production"], ".env")
register_by_name(["Makefile", "GNUmakefile"], ".sh")
