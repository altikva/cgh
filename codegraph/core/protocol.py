# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Backend-neutral graph database protocols. The concrete
# implementations are DuckDB (DuckDBGraphDB in core/db_duckdb.py) and
# SQLite (SQLiteGraphDB in core/db_sqlite.py).

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
      - DuckDBGraphDB   (default, see core/db_duckdb.py)
      - SQLiteGraphDB   (standalone binary, see core/db_sqlite.py)
    """

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        """Execute a backend-native SQL query and return a row-iterable
        result. Most write paths should use the higher-level helpers
        below; ``execute`` stays the escape hatch for read queries.
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

    def ensure_edges(self, edge_type: str, pairs: list[tuple[Any, ...]]) -> None:
        """Batched ``ensure_edge`` of (src_key, dst_key, *props) rows, the
        props in the edge's prop_columns order (none for an edge without
        properties). Duplicates are ignored."""

    def purge_file_data(self, file_path: str) -> None:
        """Delete every node + edge associated with ``file_path``.

        Deletes across all node labels keyed on file_path, plus the
        File-keyed IMPORTS edges, the file's call sites and its name
        references. Used by the indexer before re-indexing a changed file.
        """

    # --- Writer stamps ------------------------------------------------------
    # An indexer that predates the call_site / name_ref tables (cgh 0.15
    # after a rollback) rewrites a file's nodes but not its references. The
    # stamp records the File mtime each file had when this format indexed it,
    # so a later run can tell which files an older writer touched since.

    def stamp_file(self, file_path: str, mtime: float) -> None:
        """Record that ``file_path`` was fully indexed at ``mtime``.
        purge_file_data drops the stamp too."""

    def unstamped_files(self) -> tuple[list[str], list[str]]:
        """(stale, orphans): indexed files whose stamp is missing or taken at
        another mtime, and paths holding references or a stamp but no
        indexed File node any more."""

    # --- Name references ----------------------------------------------------
    # INHERITS, MD_REFS_*, MD_LINKS_TO and cross-file IMPLEMENTED_BY edges
    # are resolved by name. Keeping the references lets the indexer link them
    # from the target's side when the target is indexed later or reindexed.

    def replace_name_refs(
        self, file_path: str, rows: list[tuple[str, str, str, str]]
    ) -> None:
        """Replace the name references recorded for ``file_path`` with
        ``rows`` of (kind, from_id, name, extra)."""

    def name_refs_into(
        self, names: list[str], exclude_file: str
    ) -> list[tuple[str, str, str, str, str]]:
        """Name references outside ``exclude_file`` whose name is in
        ``names``, as (kind, from_id, file_path, name, extra) rows."""

    def name_refs_of_kind(self, kind: str) -> list[tuple[str, str, str]]:
        """Every name reference of ``kind`` in the graph, as (from_id, name,
        extra) rows. Read by the endpoints query for the router prefixes."""

    def node_keys_matching(
        self, label: str, field: str, values: list[Any]
    ) -> list[tuple[Any, Any]]:
        """(key, field value) of every ``label`` node whose ``field`` is in
        ``values``. Batched counterpart of ``find_node_keys``."""

    # --- Call sites ---------------------------------------------------------
    # CALLS edges are derived from call sites. Keeping the sites lets the
    # indexer relink callers in other files whenever a callee's file is
    # (re)indexed, independent of the order files are indexed in.

    def replace_call_sites(self, file_path: str, rows: list[tuple[str, ...]]) -> None:
        """Replace the call sites recorded for ``file_path`` with ``rows`` of
        (from_id, name, to_id, kind, hint, ctx); missing trailing values are
        "". ``to_id`` is "" for a site resolved by callee name, or the target
        Function id for a precisely resolved one. kind, hint and ctx carry
        the call's shape (see indexer._call_site_rows). purge_file_data drops
        a file's call sites too.
        """

    def call_sites_into(
        self, names: list[str], ids: list[str], exclude_file: str
    ) -> list[tuple[str, ...]]:
        """Call sites outside ``exclude_file`` that can target it: by-name
        sites whose name is in ``names`` plus resolved sites whose to_id is
        in ``ids``. Rows are (from_id, file_path, name, to_id, kind, hint,
        ctx).
        """

    def function_defs_named(self, names: list[str]) -> list[tuple[str, str, str]]:
        """Every Function whose name is in ``names``, as (id, name, file_path)."""

    def call_targets_named(
        self, names: list[str]
    ) -> list[tuple[str, str, str, str, tuple[str, ...]]]:
        """Every Function whose name is in ``names``, as (id, name, file_path,
        class id, base names): the class id is "" for a function that is no
        method, the base names are its class's recorded "inherits" names."""

    def class_bases_named(self, names: list[str]) -> list[tuple[str, str]]:
        """(class name, base name as written) for every recorded base of the
        Classes whose name is in ``names``."""

    def class_children_named(self, names: list[str]) -> list[tuple[str, str]]:
        """(class name, base name as written) of the Classes recording a
        base named in ``names``, bare or dotted (``m.Base`` for "Base");
        the caller drops a dotted base whose last segment differs."""

    def call_site_names_on(self, classes: list[str]) -> list[str]:
        """The distinct called names of the by-name call sites on a known
        class (kind "cls") whose class is in ``classes``."""

    def function_names_in(self, file_path: str) -> list[str]:
        """The distinct names of the Functions ``file_path`` defines."""

    def calls_by_name(
        self, names: list[str], exclude_file: str
    ) -> list[tuple[str, str]]:
        """CALLS edges (from_id, to_id) into a function named in ``names``,
        from a function with a by-name call site outside ``exclude_file``
        calling one of ``names``."""

    def delete_calls(self, pairs: list[tuple[str, str]]) -> None:
        """Delete the CALLS edges ``pairs`` of (from_id, to_id)."""

    def name_refs_from(self, kind: str, paths: list[str]) -> list[tuple[str, str]]:
        """(file_path, name) of the ``kind`` name references recorded for
        the files ``paths``."""

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
        with that prefix before the result is returned, used by the
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

        Used by graph_stats and friends, cheaper than fetching every
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
