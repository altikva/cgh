# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Backend-neutral graph database protocols. The concrete
# implementation today is Kuzu (KuzuGraphDB in core/db_kuzu.py); a
# DuckDB backend is being added in follow-up PRs to drop the Kuzu
# dependency entirely.

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryResult(Protocol):
    """A row-iterable result of a graph query.

    The methods mirror what cgh callers actually use today. New backends
    must return objects that expose at least these.
    """

    def has_next(self) -> bool:
        """True if get_next() will return another row."""

    def get_next(self) -> list[Any]:
        """Pop the next row as a list of column values."""

    def get_column_names(self) -> list[str]:
        """The column names of the result, in order."""


@runtime_checkable
class GraphDB(Protocol):
    """A graph database connection.

    cgh treats this connection as single-writer + many-readers. The
    indexer holds the write connection for the owner process; MCP tools
    and CLI commands open read-only connections via the same interface.

    Concrete implementations:
      - KuzuGraphDB     (current, see core/db_kuzu.py)
      - DuckDBGraphDB   (planned, see core/db_duckdb.py)
    """

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        """Execute a query and return a row-iterable result.

        ``query`` is in the backend's native dialect (Cypher for Kuzu,
        SQL for DuckDB). Higher-level helpers in MCP tools should
        eventually move to a backend-neutral query layer, but for now
        each tool emits a backend-aware query string.
        """

    def close(self) -> None:
        """Release the underlying connection / lock."""
