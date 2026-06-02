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
    conn,
    include_private: bool = False,
    file_filter: str | None = None,
) -> list[DeadSymbol]:
    """Find functions and classes with no incoming CALLS / INHERITS edges."""
    dead: list[DeadSymbol] = []
    contains = {"file_path": file_filter} if file_filter else None

    # Dead functions: no incoming CALLS edge, not an entry point, not private
    for row in conn.find_nodes_without_incoming(
        "Function",
        "CALLS",
        contains=contains,
        exclude_name_prefix=None if include_private else "_",
        return_fields=["name", "file_path", "start_line", "end_line"],
    ):
        name = row["name"]
        if name in _ENTRY_POINTS:
            continue
        dead.append(
            DeadSymbol(
                kind="function",
                name=name,
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                reason="no callers",
            )
        )

    # Dead classes: no incoming INHERITS edge
    for row in conn.find_nodes_without_incoming(
        "Class",
        "INHERITS",
        contains=contains,
        return_fields=["name", "file_path", "start_line", "end_line"],
    ):
        dead.append(
            DeadSymbol(
                kind="class",
                name=row["name"],
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                reason="no subclasses",
            )
        )

    return dead
