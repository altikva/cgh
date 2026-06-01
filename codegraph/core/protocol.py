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
        """Execute a backend-native query and return a row-iterable result.

        ``query`` is Cypher for Kuzu, SQL for DuckDB. Most write paths
        should use the higher-level helpers below; ``execute`` stays the
        escape hatch for read queries and Kuzu-specific work until the
        full query layer is ported.
        """

    def close(self) -> None:
        """Release the underlying connection / lock."""

    # --- Write helpers (backend-neutral) -----------------------------------

    def upsert_node(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        props: dict[str, Any],
    ) -> None:
        """Insert or update a node identified by (label, key_field=key_value).

        On Kuzu this becomes ``MERGE (n:Label {key_field: $k}) SET <props>``.
        On DuckDB it's ``INSERT INTO label_table (...) VALUES (...) ON
        CONFLICT (key_field) DO UPDATE SET ...``.
        """

    def ensure_edge(
        self,
        edge_type: str,
        src_key_value: Any,
        dst_key_value: Any,
        edge_props: dict[str, Any] | None = None,
    ) -> None:
        """Create an edge between two existing nodes if it doesn't exist.

        The edge type carries enough information to identify the source
        and destination label + key field. Property-carrying edges (e.g.
        IMPORTS with a symbol field) include them via ``edge_props``.
        """

    def purge_file_data(self, file_path: str) -> None:
        """Delete every node + edge associated with ``file_path``.

        Equivalent to Kuzu's DETACH DELETE across all node labels keyed
        on file_path, plus the File-keyed IMPORTS edges. Used by the
        indexer before re-indexing a changed file.
        """

    def find_node_keys(
        self,
        label: str,
        where_field: str,
        where_value: Any,
    ) -> list[Any]:
        """Return every key (PRIMARY KEY) value for nodes of ``label`` where
        ``where_field`` equals ``where_value``.

        Replaces the cgh resolver pattern that does
        ``MATCH (n:Label) WHERE n.field = $v RETURN n.id`` to feed an
        ensure_edge loop.
        """

    def query_node_field(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        return_field: str,
    ) -> Any | None:
        """Return one field of a node identified by (label, key_field=value),
        or None if no such node exists. Used for cheap read-back queries
        like the indexer's mtime check.
        """

    def list_node_fields(
        self,
        label: str,
        return_fields: list[str],
    ) -> list[list[Any]]:
        """Return ``return_fields`` for every node of ``label``. Used by
        the indexer's full-repo blob-SHA listing pass; keep the result
        small to avoid loading the whole graph into memory.
        """

    def delete_file_completely(self, file_path: str) -> None:
        """Like purge_file_data, but also drops the File node itself.
        Used when a file is removed from the working tree.
        """
