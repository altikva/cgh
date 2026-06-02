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

    def find_nodes(
        self,
        label: str,
        where: dict[str, Any] | None = None,
        contains: dict[str, Any] | None = None,
        return_fields: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching nodes as a list of field-keyed dicts.

        - ``where``: ``{field: value}`` exact match, AND across fields.
        - ``contains``: ``{field: value}`` substring search, OR across fields.
        - ``return_fields``: which columns to include. None = all columns
          for the label.
        - ``order_by``: list of field names to sort ascending by.
        - ``limit``: optional row cap.

        Used by symbol_lookup, search_symbols, doc search, and similar
        "find me nodes matching X" tools.
        """

    def find_nodes_without_incoming(
        self,
        label: str,
        edge_type: str,
        contains: dict[str, Any] | None = None,
        exclude_name_prefix: str | None = None,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return nodes of ``label`` that have no incoming edge of
        ``edge_type``.

        ``exclude_name_prefix`` (e.g. ``"_"``) filters out names starting
        with that prefix before the result is returned — used by the
        dead-code detector to skip dunder / underscore methods that
        wouldn't be called from outside.

        Used by analysis.dead_code to find functions with no callers and
        classes with no subclasses.
        """

    def find_neighbors(
        self,
        edge_type: str,
        src_key: Any | None = None,
        dst_key: Any | None = None,
        src_where: dict[str, Any] | None = None,
        dst_where: dict[str, Any] | None = None,
        return_src: list[str] | None = None,
        return_dst: list[str] | None = None,
        return_edge: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Walk an edge type with optional anchoring on either side.

        Result dicts use prefixed keys: ``src_<field>``, ``dst_<field>``,
        ``edge_<field>``. The caller picks which prefixed fields they
        want via the three ``return_*`` lists; the rest are dropped.
        ``limit`` caps the row count.

        Used by find_callers, find_callees, imports_of, who_imports,
        and similar "walk an edge from / to a known anchor" tools.
        """

    def count_nodes(self, label: str, where: dict[str, Any] | None = None) -> int:
        """Count nodes of ``label`` matching the optional ``where`` filter.

        Used by graph_stats and friends — cheaper than fetching every
        row just to call ``len()``.
        """

    def count_edges(self, edge_type: str) -> int:
        """Count edges of ``edge_type``. Used by the CLI stats display
        which surfaces per-edge-type counts."""

    def reach_via_edge(
        self,
        edge_type: str,
        start_key: Any,
        max_depth: int = 1,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Transitive reach along ``edge_type`` from ``start_key``.

        Returns distinct destinations within ``max_depth`` hops.
        Used by subgraph reach and the recursive IMPORTS query.
        """
