# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: DuckDB backend implementing the GraphDB protocol.
#
# Status: schema works end-to-end (CREATE TABLE / SELECT / INSERT all run).
# The indexer and MCP tools still emit Cypher and therefore can't yet talk to
# this backend — that's the next PR in the chain. Selecting this backend
# today via `CGH_DB=duckdb` is intended for the DuckDB-port work, not
# end-user use.

from __future__ import annotations

from typing import Any

import duckdb

from codegraph.core.protocol import QueryResult
from codegraph.core.schema_duckdb import init_schema


class DuckDBQueryResult:
    """Adapter wrapping a duckdb cursor result to match the QueryResult protocol.

    DuckDB returns column names + rows on the cursor itself rather than on
    a separate result object; we materialise both up front so the
    iterator interface mirrors Kuzu's exactly.
    """

    def __init__(self, cursor: duckdb.DuckDBPyConnection | duckdb.DuckDBPyRelation) -> None:
        # description: list of (name, type, ...) per stdlib DB-API
        desc = cursor.description or []
        self._columns: list[str] = [d[0] for d in desc]
        self._rows: list[tuple[Any, ...]] = cursor.fetchall()
        self._cursor_pos = 0

    def has_next(self) -> bool:
        return self._cursor_pos < len(self._rows)

    def get_next(self) -> list[Any]:
        row = self._rows[self._cursor_pos]
        self._cursor_pos += 1
        return list(row)

    def get_column_names(self) -> list[str]:
        return list(self._columns)


class DuckDBGraphDB:
    """Adapter wrapping duckdb.DuckDBPyConnection to match the GraphDB protocol.

    The schema is created on construction so callers can rely on every
    table existing on first execute() call.
    """

    def __init__(self, db_path: str, read_only: bool = False) -> None:
        self._conn = duckdb.connect(db_path, read_only=read_only)
        if not read_only:
            init_schema(self._conn)

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        # DuckDB's positional-only "?" placeholder doesn't accept a dict.
        # MCP tools using named parameters ($name) need to use named-style
        # statements via prepare(); for the first port pass we accept both
        # styles: dict params go through execute(query, list_of_values)
        # rendered from the dict.
        if params is None:
            cursor = self._conn.execute(query)
        elif isinstance(params, dict):
            cursor = self._conn.execute(query, params)
        else:
            cursor = self._conn.execute(query, params)
        return DuckDBQueryResult(cursor)

    def close(self) -> None:
        self._conn.close()

    # --- Write helpers --------------------------------------------------

    def upsert_node(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        props: dict[str, Any],
    ) -> None:
        """``INSERT ... ON CONFLICT DO UPDATE`` to match Kuzu's MERGE+SET."""
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        table = NODES[label].table

        cols = [key_field, *props.keys()]
        values = [key_value, *props.values()]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        if props:
            update_clause = ", ".join(f"{c} = excluded.{c}" for c in props)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({key_field}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({key_field}) DO NOTHING"
            )
        self._conn.execute(sql, values)

    def ensure_edge(
        self,
        edge_type: str,
        src_key_value: Any,
        dst_key_value: Any,
        edge_props: dict[str, Any] | None = None,
    ) -> None:
        """``INSERT ... ON CONFLICT DO NOTHING`` against the edge table."""
        from codegraph.core.graph_model import EDGES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        spec = EDGES[edge_type]

        cols = [spec.src_column, spec.dst_column, *spec.prop_columns]
        values: list[Any] = [src_key_value, dst_key_value]
        if spec.prop_columns:
            assert edge_props is not None, (
                f"Edge {edge_type!r} requires props {spec.prop_columns}, got None"
            )
            for col in spec.prop_columns:
                values.append(edge_props.get(col, ""))
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        sql = (
            f"INSERT INTO {spec.table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT DO NOTHING"
        )
        self._conn.execute(sql, values)

    def purge_file_data(self, file_path: str) -> None:
        """Delete all data tied to file_path. Edges first, then nodes."""
        from codegraph.core.graph_model import NODES, edges_touching

        # Step 1: for each node label that's keyed by file_path, look up
        # the affected node ids, then drop every edge touching that label.
        for spec in NODES.values():
            if not spec.has_file_path:
                continue
            # Collect ids of nodes belonging to this file so we can
            # delete edges that reference them by id (vs file_path).
            ids_rows = self._conn.execute(
                f"SELECT id FROM {spec.table} WHERE file_path = ?",
                [file_path],
            ).fetchall()
            ids = [r[0] for r in ids_rows]

            for edge in edges_touching(spec.label):
                # Determine which side of the edge points at this label.
                # If both sides do (e.g. CALLS Function->Function), purge both.
                if edge.src_label == spec.label:
                    column = edge.src_column
                    if column.endswith("_path"):
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} = ?",
                            [file_path],
                        )
                    elif ids:
                        placeholders = ", ".join("?" for _ in ids)
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} IN ({placeholders})",
                            ids,
                        )
                if edge.dst_label == spec.label and edge.src_label != spec.label:
                    column = edge.dst_column
                    if column.endswith("_path"):
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} = ?",
                            [file_path],
                        )
                    elif ids:
                        placeholders = ", ".join("?" for _ in ids)
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} IN ({placeholders})",
                            ids,
                        )

        # Step 2: delete the node rows themselves (file_path-keyed ones).
        for spec in NODES.values():
            if not spec.has_file_path:
                continue
            self._conn.execute(
                f"DELETE FROM {spec.table} WHERE file_path = ?",
                [file_path],
            )

        # Step 3: drop File-level outbound IMPORTS edges. The File node
        # itself stays — the indexer reuses its primary key when re-upserting.
        self._conn.execute("DELETE FROM edge_imports WHERE from_path = ?", [file_path])

    def find_node_keys(
        self,
        label: str,
        where_field: str,
        where_value: Any,
    ) -> list[Any]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        rows = self._conn.execute(
            f"SELECT {spec.key_field} FROM {spec.table} WHERE {where_field} = ?",
            [where_value],
        ).fetchall()
        return [r[0] for r in rows]

    def query_node_field(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        return_field: str,
    ) -> Any | None:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        row = self._conn.execute(
            f"SELECT {return_field} FROM {spec.table} WHERE {key_field} = ?",
            [key_value],
        ).fetchone()
        return None if row is None else row[0]

    def list_node_fields(
        self,
        label: str,
        return_fields: list[str],
    ) -> list[list[Any]]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        cols = ", ".join(return_fields)
        rows = self._conn.execute(f"SELECT {cols} FROM {spec.table}").fetchall()
        return [list(r) for r in rows]

    def delete_file_completely(self, file_path: str) -> None:
        self.purge_file_data(file_path)
        # Also drop inbound IMPORTS edges (purge only handles outbound).
        self._conn.execute("DELETE FROM edge_imports WHERE to_path = ?", [file_path])
        self._conn.execute("DELETE FROM file WHERE path = ?", [file_path])

    # Escape hatch for tooling that needs the raw DuckDB connection
    # (e.g. ATTACH for federation, EXPLAIN ANALYZE). Symmetric with
    # KuzuGraphDB.raw — both go away when the Kuzu code path is deleted.
    @property
    def raw(self) -> duckdb.DuckDBPyConnection:
        return self._conn


__all__ = ["DuckDBGraphDB", "DuckDBQueryResult"]
