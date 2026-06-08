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
# this backend: that's the next PR in the chain. Selecting this backend
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
                # Also purge the inbound side. For self-referential edges
                # (CALLS/INHERITS Function->Function) src and dst share a label
                # but use different columns (from_id/to_id), so this removes
                # stale callers pointing INTO this file's symbols, matching
                # Kuzu's DETACH DELETE. Without it, find_callers keeps ghosts.
                if edge.dst_label == spec.label:
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
        # itself stays, the indexer reuses its primary key when re-upserting.
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

    def find_nodes(
        self,
        label: str,
        where: dict[str, Any] | None = None,
        contains: dict[str, Any] | None = None,
        return_fields: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]

        params: list[Any] = []
        clauses: list[str] = []
        if where:
            for field, value in where.items():
                clauses.append(f"{field} = ?")
                params.append(value)
        if contains:
            sub_clauses = []
            for field, value in contains.items():
                # Wrap value with % for DuckDB's LIKE, case-sensitive.
                # Cypher CONTAINS is case-sensitive too, so this matches.
                sub_clauses.append(f"{field} LIKE ?")
                params.append(f"%{value}%")
            if sub_clauses:
                clauses.append("(" + " OR ".join(sub_clauses) + ")")

        select_clause = ", ".join(return_fields) if return_fields and return_fields != ["*"] else "*"
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        order_clause = "ORDER BY " + ", ".join(order_by) if order_by else ""
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        sql = (
            f"SELECT {select_clause} FROM {spec.table} "
            f"{where_clause} {order_clause} {limit_clause}"
        )

        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def count_nodes(self, label: str, where: dict[str, Any] | None = None) -> int:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        params: list[Any] = []
        clauses: list[str] = []
        if where:
            for field, value in where.items():
                clauses.append(f"{field} = ?")
                params.append(value)
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"SELECT count(*) FROM {spec.table} {where_clause}"
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def count_edges(self, edge_type: str) -> int:
        from codegraph.core.graph_model import EDGES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        spec = EDGES[edge_type]
        row = self._conn.execute(f"SELECT count(*) FROM {spec.table}").fetchone()
        return int(row[0]) if row else 0

    def find_nodes_without_incoming(
        self,
        label: str,
        edge_type: str,
        contains: dict[str, Any] | None = None,
        exclude_name_prefix: str | None = None,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import EDGES, NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        spec = NODES[label]
        edge = EDGES[edge_type]

        if not return_fields:
            return_fields = [spec.key_field, "name", "file_path", "start_line", "end_line"]

        params: list[Any] = []
        clauses: list[str] = []
        # LEFT ANTI-JOIN via NOT EXISTS, works on every DuckDB version.
        clauses.append(
            f"NOT EXISTS (SELECT 1 FROM {edge.table} e WHERE e.{edge.dst_column} = n.{spec.key_field})"
        )
        if exclude_name_prefix:
            # Plain SUBSTR avoids LIKE wildcard collision (`_` and `%` are
            # LIKE metachars in DuckDB; user code that calls this with
            # exclude_name_prefix='_' wants the literal underscore).
            clauses.append("substr(n.name, 1, ?) <> ?")
            params.append(len(exclude_name_prefix))
            params.append(exclude_name_prefix)
        if contains:
            for field, value in contains.items():
                clauses.append(f"n.{field} LIKE ?")
                params.append(f"%{value}%")

        select_clause = ", ".join(f"n.{f}" for f in return_fields)
        sql = (
            f"SELECT {select_clause} FROM {spec.table} n WHERE {' AND '.join(clauses)}"
        )
        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

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
        from codegraph.core.graph_model import EDGES, NODES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        edge = EDGES[edge_type]
        src = NODES[edge.src_label]
        dst = NODES[edge.dst_label]

        select_parts: list[str] = []
        for f in return_src or []:
            select_parts.append(f"a.{f} AS src_{f}")
        for f in return_dst or []:
            select_parts.append(f"b.{f} AS dst_{f}")
        for f in return_edge or []:
            select_parts.append(f"e.{f} AS edge_{f}")
        if not select_parts:
            select_parts = [f"b.{dst.key_field} AS dst_{dst.key_field}"]

        params: list[Any] = []
        clauses: list[str] = []
        clauses.append(f"e.{edge.src_column} = a.{src.key_field}")
        clauses.append(f"e.{edge.dst_column} = b.{dst.key_field}")
        if src_key is not None:
            clauses.append(f"a.{src.key_field} = ?")
            params.append(src_key)
        if dst_key is not None:
            clauses.append(f"b.{dst.key_field} = ?")
            params.append(dst_key)
        if src_where:
            for field, value in src_where.items():
                clauses.append(f"a.{field} = ?")
                params.append(value)
        if dst_where:
            for field, value in dst_where.items():
                clauses.append(f"b.{field} = ?")
                params.append(value)

        select_clause = ", ".join(select_parts)
        where_clause = "WHERE " + " AND ".join(clauses)
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        sql = (
            f"SELECT {select_clause} FROM {edge.table} e, "
            f"{src.table} a, {dst.table} b {where_clause} {limit_clause}"
        )

        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def reach_via_edge(
        self,
        edge_type: str,
        start_key: Any,
        max_depth: int = 1,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import EDGES, NODES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        edge = EDGES[edge_type]
        dst = NODES[edge.dst_label]
        return_fields = return_fields or [dst.key_field]
        return_clause = ", ".join(f"dst_node.{f}" for f in return_fields)

        # Recursive CTE that walks edge.table to depth max_depth.
        sql = f"""
        WITH RECURSIVE reach(key, depth) AS (
            SELECT {edge.dst_column}, 1 FROM {edge.table}
            WHERE {edge.src_column} = ?
            UNION ALL
            SELECT e.{edge.dst_column}, r.depth + 1
            FROM {edge.table} e
            JOIN reach r ON e.{edge.src_column} = r.key
            WHERE r.depth < ?
        )
        SELECT DISTINCT {return_clause}
        FROM reach
        JOIN {dst.table} dst_node ON dst_node.{dst.key_field} = reach.key
        """
        cursor = self._conn.execute(sql, [start_key, int(max_depth)])
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    # Escape hatch for tooling that needs the raw DuckDB connection
    # (e.g. ATTACH for federation, EXPLAIN ANALYZE). Symmetric with
    # KuzuGraphDB.raw, both go away when the Kuzu code path is deleted.
    @property
    def raw(self) -> duckdb.DuckDBPyConnection:
        return self._conn


__all__ = ["DuckDBGraphDB", "DuckDBQueryResult"]
