# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Kuzu backend implementing the GraphDB protocol.
#
# This is a thin passthrough adapter — Kuzu's native Connection already
# matches the protocol structurally, so the adapter exists primarily as
# a named seam for the DuckDB backend to slot in alongside.

from __future__ import annotations

from typing import Any

import kuzu

from codegraph.core.protocol import GraphDB, QueryResult


class KuzuQueryResult:
    """Adapter wrapping kuzu.QueryResult to match the QueryResult protocol."""

    def __init__(self, inner: kuzu.QueryResult) -> None:
        self._inner = inner

    def has_next(self) -> bool:
        return self._inner.has_next()

    def get_next(self) -> list[Any]:
        return self._inner.get_next()

    def get_column_names(self) -> list[str]:
        return self._inner.get_column_names()


class KuzuGraphDB:
    """Adapter wrapping kuzu.Connection to match the GraphDB protocol."""

    def __init__(self, inner: kuzu.Connection) -> None:
        self._inner = inner

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        # Kuzu accepts an optional dict of parameters.
        result = self._inner.execute(query, params) if params is not None else self._inner.execute(query)
        return KuzuQueryResult(result)

    def close(self) -> None:
        self._inner.close()

    # Escape hatch for code that still needs raw Kuzu objects (Kuzu-specific
    # helpers, federation that re-opens DBs read-only). Will be removed
    # alongside Kuzu in the 0.5 release that finishes the backend swap.
    @property
    def raw(self) -> kuzu.Connection:
        return self._inner


__all__ = ["KuzuGraphDB", "KuzuQueryResult", "GraphDB", "QueryResult"]
