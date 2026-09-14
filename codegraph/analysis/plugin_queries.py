# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read-only graph queries offered to plugins through the public
#              plugin API. High-level and connection-managed on purpose: a
#              plugin never touches the GraphDB protocol, it calls a function
#              that returns plain dicts. Parent-scope only (no federation):
#              the one consumer, reference selection for code generation,
#              picks a file from the working tree it will write into.

from __future__ import annotations

from pathlib import Path


def find_symbol_files(
    repo_root: str | Path | None,
    query: str,
    *,
    limit: int = 20,
) -> list[dict] | None:
    """Files that define a function or class whose name contains ``query``.

    Returns a list of ``{kind, name, file, line}`` dicts, or ``None`` when
    the graph cannot be read (no index yet, or an owner holds the write
    lock and this call is from a separate process). ``None`` is a signal to
    fall back, not an error: the caller decides what to do without the
    graph. An empty list means the graph was readable and nothing matched.
    """
    q = query.strip()
    if not q:
        return []

    from codegraph.core.db import get_readonly_connection

    conn = get_readonly_connection(repo_root)
    if conn is None:
        return None

    out: list[dict] = []
    for label, kind in (("Function", "function"), ("Class", "class")):
        for row in conn.find_nodes(
            label,
            contains={"name": q},
            return_fields=["name", "file_path", "start_line"],
            limit=limit,
        ):
            out.append(
                {
                    "kind": kind,
                    "name": row["name"],
                    "file": row["file_path"],
                    "line": row["start_line"],
                }
            )
    return out
