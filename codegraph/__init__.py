"""codegraph, Local code graph index for AI coding assistants."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed package version (pyproject.toml).
    # Avoids the banner drifting from the real version on a release bump.
    __version__ = version("cgh")
except PackageNotFoundError:
    # Running from a source tree with no installed metadata.
    __version__ = "0.0.0+source"
