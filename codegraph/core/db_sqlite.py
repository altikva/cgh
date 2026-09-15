# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: SQLite backend implementing the GraphDB protocol. A near-line-for
#              -line port of the DuckDB backend: both speak SQL, so the schema,
#              the ON CONFLICT upserts, the recursive-CTE traversals and the
#              "?"-parameter queries are identical. The point of this backend is
#              weight: SQLite is already bundled for FTS, so it adds ~0 to a
#              frozen binary, where DuckDB's native library is ~50 MB. Two
#              dialect adjustments vs DuckDB: LIKE is forced case-sensitive to
#              match DuckDB's contains semantics, and writes run in WAL with a
#              commit on close.

from __future__ import annotations

import re
import sqlite3
from typing import Any

from codegraph.core.protocol import QueryResult
from codegraph.core.utils import checked_identifier as _ident

# The DDL is dialect-neutral enough that DuckDB and SQLite share it verbatim
# (SQLite gives DOUBLE/BIGINT the usual affinities; composite PKs, partial
# indexes and IF NOT EXISTS all work). Kept inline so this backend has no
# import dependency on the duckdb-importing schema module.
_NODE_TABLES = [
    """CREATE TABLE IF NOT EXISTS file (
        path TEXT PRIMARY KEY, lang TEXT, mtime DOUBLE, git_blob_sha TEXT,
        role TEXT, layer TEXT, module_doc TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS endpoint (
        id TEXT PRIMARY KEY, method TEXT, path TEXT, framework TEXT,
        file_path TEXT, start_line BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS function (
        id TEXT PRIMARY KEY, name TEXT, file_path TEXT, start_line BIGINT,
        end_line BIGINT, docstring TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS class (
        id TEXT PRIMARY KEY, name TEXT, file_path TEXT, start_line BIGINT,
        end_line BIGINT, docstring TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tf_resource (
        id TEXT PRIMARY KEY, name TEXT, type TEXT, file_path TEXT,
        start_line BIGINT, end_line BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS tf_var (
        id TEXT PRIMARY KEY, name TEXT, kind TEXT, file_path TEXT, start_line BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS md_section (
        id TEXT PRIMARY KEY, title TEXT, level BIGINT, file_path TEXT,
        start_line BIGINT, end_line BIGINT, body_preview TEXT, anchor TEXT
    )""",
]

_EDGE_TABLES = [
    """CREATE TABLE IF NOT EXISTS edge_imports (
        from_path TEXT, to_path TEXT, symbol TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_path, to_path, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_fn (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_class (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_calls (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_inherits (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_has_method (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_tf_depends (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_resource (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_tfvar (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_section (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_links_to (
        from_id TEXT, to_path TEXT, label TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_path, label)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_refs_symbol (
        from_id TEXT, to_id TEXT, context TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_id, context)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_refs_class (
        from_id TEXT, to_id TEXT, context TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_id, context)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_contains_section (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_endpoint (
        from_path TEXT, to_id TEXT, PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_implemented_by (
        from_id TEXT, to_id TEXT, PRIMARY KEY (from_id, to_id)
    )""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_imports_to ON edge_imports(to_path)",
    "CREATE INDEX IF NOT EXISTS idx_calls_to ON edge_calls(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_inherits_to ON edge_inherits(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_function_file ON function(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_class_file ON class(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_md_section_file ON md_section(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_endpoint_file ON endpoint(file_path)",
    # These have no DuckDB equivalent: DuckDB's columnar scan needs no index,
    # but SQLite's nested-loop join does. Without idx_function_name, a
    # find_callers/find_callees join full-scans the edge table (measured 27 ms
    # vs 0.002 ms with it on a 58k-edge graph). The from-side calls index and
    # the name indexes let the planner anchor the join on the named symbol.
    "CREATE INDEX IF NOT EXISTS idx_function_name ON function(name)",
    "CREATE INDEX IF NOT EXISTS idx_class_name ON class(name)",
    "CREATE INDEX IF NOT EXISTS idx_calls_from ON edge_calls(from_id)",
    "CREATE INDEX IF NOT EXISTS idx_inherits_from ON edge_inherits(from_id)",
]

_NAMED_PARAM = re.compile(r"\$(\w+)")


class SQLiteQueryResult:
    """Adapter wrapping a sqlite3 cursor to match the QueryResult protocol.
    Materialises rows + column names up front, like the DuckDB adapter."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        desc = cursor.description or []
        self._columns: list[str] = [d[0] for d in desc]
        self._rows: list[tuple[Any, ...]] = cursor.fetchall()
        self._pos = 0

    def has_next(self) -> bool:
        return self._pos < len(self._rows)

    def get_next(self) -> list[Any]:
        row = self._rows[self._pos]
        self._pos += 1
        return list(row)

    def get_column_names(self) -> list[str]:
        return list(self._columns)


class SQLiteGraphDB:
    """Adapter wrapping sqlite3.Connection to match the GraphDB protocol."""

    def __init__(self, db_path: str, read_only: bool = False) -> None:
        if read_only:
            # Open read-only via URI so a reader never creates the file or
            # takes a write lock (mirrors DuckDB's read_only connect).
            self._conn = sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
            )
            self._read_only = True
        else:
            self._read_only = False
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        # DuckDB's LIKE is case-sensitive and cgh's contains queries rely on
        # that (Cypher CONTAINS is case-sensitive too). SQLite LIKE is
        # case-insensitive by default, so force the match.
        self._conn.execute("PRAGMA case_sensitive_like=ON")
        if not read_only:
            for ddl in _NODE_TABLES + _EDGE_TABLES + _INDEXES:
                self._conn.execute(ddl)
            self._conn.commit()

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        if params is None:
            cursor = self._conn.execute(query)
        elif isinstance(params, dict):
            # DuckDB raw queries use $name; SQLite's binding wants :name.
            cursor = self._conn.execute(_NAMED_PARAM.sub(r":\1", query), params)
        else:
            cursor = self._conn.execute(query, params)
        return SQLiteQueryResult(cursor)

    def close(self) -> None:
        # SQLite batches writes in a transaction; commit before releasing so
        # the owner's work is durable (DuckDB auto-commits, this matches it).
        # PRAGMA optimize refreshes the stats the query planner uses to pick a
        # join order, so the next reader gets index-anchored plans, not scans.
        try:
            if not self._read_only:
                self._conn.execute("PRAGMA optimize")
            self._conn.commit()
        except sqlite3.Error:
            pass
        self._conn.close()

    # --- Write helpers --------------------------------------------------

    def upsert_node(
        self, label: str, key_field: str, key_value: Any, props: dict[str, Any]
    ) -> None:
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
        from codegraph.core.graph_model import EDGES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        spec = EDGES[edge_type]

        cols = [spec.src_column, spec.dst_column, *spec.prop_columns]
        values: list[Any] = [src_key_value, dst_key_value]
        if spec.prop_columns:
            if edge_props is None:
                raise ValueError(
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
        from codegraph.core.graph_model import NODES, edges_touching

        for spec in NODES.values():
            if not spec.has_file_path:
                continue
            ids_rows = self._conn.execute(
                f"SELECT id FROM {spec.table} WHERE file_path = ?", [file_path]
            ).fetchall()
            ids = [r[0] for r in ids_rows]

            for edge in edges_touching(spec.label):
                if edge.src_label == spec.label:
                    column = edge.src_column
                    if column.endswith("_path"):
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} = ?", [file_path]
                        )
                    elif ids:
                        ph = ", ".join("?" for _ in ids)
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} IN ({ph})", ids
                        )
                if edge.dst_label == spec.label:
                    column = edge.dst_column
                    if column.endswith("_path"):
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} = ?", [file_path]
                        )
                    elif ids:
                        ph = ", ".join("?" for _ in ids)
                        self._conn.execute(
                            f"DELETE FROM {edge.table} WHERE {column} IN ({ph})", ids
                        )

        for spec in NODES.values():
            if not spec.has_file_path:
                continue
            self._conn.execute(
                f"DELETE FROM {spec.table} WHERE file_path = ?", [file_path]
            )

        self._conn.execute("DELETE FROM edge_imports WHERE from_path = ?", [file_path])

    def find_node_keys(
        self, label: str, where_field: str, where_value: Any
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
        self, label: str, key_field: str, key_value: Any, return_field: str
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

    def list_node_fields(self, label: str, return_fields: list[str]) -> list[list[Any]]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        cols = ", ".join(_ident(f) for f in return_fields)
        rows = self._conn.execute(f"SELECT {cols} FROM {spec.table}").fetchall()
        return [list(r) for r in rows]

    def delete_file_completely(self, file_path: str) -> None:
        self.purge_file_data(file_path)
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
                clauses.append(f"{_ident(field)} = ?")
                params.append(value)
        if contains:
            sub_clauses = []
            for field, value in contains.items():
                sub_clauses.append(f"{_ident(field)} LIKE ?")
                params.append(f"%{value}%")
            if sub_clauses:
                clauses.append("(" + " OR ".join(sub_clauses) + ")")

        select_clause = (
            ", ".join(_ident(f) for f in return_fields)
            if return_fields and return_fields != ["*"]
            else "*"
        )
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        order_clause = (
            "ORDER BY " + ", ".join(_ident(f) for f in order_by) if order_by else ""
        )
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        sql = (
            f"SELECT {select_clause} FROM {spec.table} "
            f"{where_clause} {order_clause} {limit_clause}"
        )
        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]

    def count_nodes(self, label: str, where: dict[str, Any] | None = None) -> int:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        params: list[Any] = []
        clauses: list[str] = []
        if where:
            for field, value in where.items():
                clauses.append(f"{_ident(field)} = ?")
                params.append(value)
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        row = self._conn.execute(
            f"SELECT count(*) FROM {spec.table} {where_clause}", params
        ).fetchone()
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
            return_fields = [
                spec.key_field,
                "name",
                "file_path",
                "start_line",
                "end_line",
            ]

        params: list[Any] = []
        clauses: list[str] = [
            f"NOT EXISTS (SELECT 1 FROM {edge.table} e "
            f"WHERE e.{edge.dst_column} = n.{spec.key_field})"
        ]
        if exclude_name_prefix:
            clauses.append("substr(n.name, 1, ?) <> ?")
            params.append(len(exclude_name_prefix))
            params.append(exclude_name_prefix)
        if contains:
            for field, value in contains.items():
                clauses.append(f"n.{_ident(field)} LIKE ?")
                params.append(f"%{value}%")

        select_clause = ", ".join(f"n.{_ident(f)}" for f in return_fields)
        sql = (
            f"SELECT {select_clause} FROM {spec.table} n WHERE {' AND '.join(clauses)}"
        )
        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in (cursor.description or [])]
        return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]

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
            select_parts.append(f"a.{_ident(f)} AS src_{f}")
        for f in return_dst or []:
            select_parts.append(f"b.{_ident(f)} AS dst_{f}")
        for f in return_edge or []:
            select_parts.append(f"e.{_ident(f)} AS edge_{f}")
        if not select_parts:
            select_parts = [f"b.{dst.key_field} AS dst_{dst.key_field}"]

        params: list[Any] = []
        clauses: list[str] = [
            f"e.{edge.src_column} = a.{src.key_field}",
            f"e.{edge.dst_column} = b.{dst.key_field}",
        ]
        if src_key is not None:
            clauses.append(f"a.{src.key_field} = ?")
            params.append(src_key)
        if dst_key is not None:
            clauses.append(f"b.{dst.key_field} = ?")
            params.append(dst_key)
        if src_where:
            for field, value in src_where.items():
                clauses.append(f"a.{_ident(field)} = ?")
                params.append(value)
        if dst_where:
            for field, value in dst_where.items():
                clauses.append(f"b.{_ident(field)} = ?")
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
        return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]

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
        return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn


__all__ = ["SQLiteGraphDB", "SQLiteQueryResult"]
