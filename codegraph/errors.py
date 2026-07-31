# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT (SDK embedding exception, see LICENSE)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The public exception hierarchy. Entrypoints (CLI, MCP,
#              SDK consumers) catch CodegraphError to shape exit codes
#              and responses; everything else is a bug and rises. New
#              raise sites in codegraph use these subclasses, never a
#              bare RuntimeError; existing sites migrate as they are
#              touched.

from __future__ import annotations


class CodegraphError(Exception):
    """Expected error originating from cgh."""


class ConfigurationError(CodegraphError):
    """Configuration is missing, unreadable, or invalid."""


class BackendError(CodegraphError):
    """The graph backend failed or is unavailable."""


class IndexingError(CodegraphError):
    """Indexing a file or repository failed in an expected way."""


class CapabilityMissing(CodegraphError, RuntimeError):
    """A requested capability has no installed provider. The message
    names the package that supplies it. Inherits RuntimeError for
    backward compatibility with early SDK consumers."""

    def __init__(self, capability: str, package: str) -> None:
        self.capability = capability
        self.package = package
        super().__init__(
            f"no installed provider for {capability!r}: pip install {package}"
        )
