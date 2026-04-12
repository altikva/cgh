# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Dead code detector — finds symbols with no incoming edges.

from __future__ import annotations

from dataclasses import dataclass

import kuzu

from .core.utils import rows as _rows

# Entry-point names that are never "dead" even without incoming edges
_ENTRY_POINTS = {
    "__init__",
    "__main__",
    "main",
    "app",
    "setup",
    "teardown",
    "conftest",
    "pytest_configure",
    "pytest_collection_modifyitems",
    "lifespan",
    "startup",
    "shutdown",
}


@dataclass
class DeadSymbol:
    kind: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    reason: str


def find_dead_code(
    conn: kuzu.Connection,
    include_private: bool = False,
    file_filter: str | None = None,
) -> list[DeadSymbol]:
    """Find functions and classes with no incoming CALLS / INHERITS edges."""
    dead: list[DeadSymbol] = []

    # Dead functions: no CALLS edge pointing to them, not an entry point
    where_clauses = ["NOT (fn)<-[:CALLS]-()"]
    if not include_private:
        where_clauses.append("NOT fn.name STARTS WITH '_'")
    if file_filter:
        where_clauses.append("fn.file_path CONTAINS $ff")

    where = " AND ".join(where_clauses)
    params = {"ff": file_filter} if file_filter else {}

    r = conn.execute(
        f"MATCH (fn:Function) WHERE {where} RETURN fn.name, fn.file_path, fn.start_line, fn.end_line",
        params,
    )
    for row in _rows(r):
        name = row["fn.name"]
        if name in _ENTRY_POINTS:
            continue
        dead.append(
            DeadSymbol(
                kind="function",
                name=name,
                file_path=row["fn.file_path"],
                start_line=row["fn.start_line"],
                end_line=row["fn.end_line"],
                reason="no callers",
            )
        )

    # Dead classes: no INHERITS edge pointing to them, no HAS_METHOD usage
    where_clauses_cls = ["NOT (c)<-[:INHERITS]-()"]
    if file_filter:
        where_clauses_cls.append("c.file_path CONTAINS $ff")
    where_cls = " AND ".join(where_clauses_cls)

    r = conn.execute(
        f"MATCH (c:Class) WHERE {where_cls} RETURN c.name, c.file_path, c.start_line, c.end_line",
        params,
    )
    for row in _rows(r):
        dead.append(
            DeadSymbol(
                kind="class",
                name=row["c.name"],
                file_path=row["c.file_path"],
                start_line=row["c.start_line"],
                end_line=row["c.end_line"],
                reason="no subclasses",
            )
        )

    return dead
