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

    # Escape hatch for tooling that needs the raw DuckDB connection
    # (e.g. ATTACH for federation, EXPLAIN ANALYZE). Symmetric with
    # KuzuGraphDB.raw — both go away when the Kuzu code path is deleted.
    @property
    def raw(self) -> duckdb.DuckDBPyConnection:
        return self._conn


__all__ = ["DuckDBGraphDB", "DuckDBQueryResult"]
