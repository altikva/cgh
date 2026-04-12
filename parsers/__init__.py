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
#   4. Done — codegraph auto-discovers it on import
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


def is_supported(path: str | Path) -> bool:
    """Check if a file can be parsed."""
    return Path(path).suffix.lower() in _REGISTRY


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
