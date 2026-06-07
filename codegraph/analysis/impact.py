# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Pure GraphDB-protocol helpers shared by the test-mapping MCP
#              tools (tests_for / untested) and the `cgh impact` CI command.
#              Computes test-to-code mapping on the fly from IMPORTS + CALLS
#              edges plus File.role, with no new edge type, plus a bounded
#              reverse-BFS over IMPORTS for blast radius. Backend-neutral:
#              every call goes through the GraphDB protocol, no raw SQL.

from __future__ import annotations

from typing import Any

# Hard caps so a pathological graph never produces an unbounded result.
TEST_ROLE = "test"
_FANOUT_CAP = 500
_REVERSE_CAP = 300


def _is_test_role(role: str | None) -> bool:
    """A File node is a test when roles.classify tagged it `test`."""
    return (role or "") == TEST_ROLE


def file_role(conn: Any, file_path: str) -> tuple[str, str]:
    """Return (role, layer) for a File node, or ("", "") when absent."""
    rows = conn.find_nodes(
        "File",
        where={"path": file_path},
        return_fields=["role", "layer"],
        limit=1,
    )
    if not rows:
        return "", ""
    return rows[0].get("role") or "", rows[0].get("layer") or ""


def resolve_target_file(conn: Any, target: str) -> str | None:
    """Resolve a symbol-or-file argument to a defining File path.

    - If ``target`` is itself a File node path, return it.
    - Else treat it as a Function / Class name and return the path of the
      first defining file found.

    Returns None when nothing matches.
    """
    hits = conn.find_nodes("File", where={"path": target}, limit=1)
    if hits:
        return target
    for label in ("Function", "Class"):
        rows = conn.find_nodes(
            label,
            where={"name": target},
            return_fields=["file_path"],
            limit=1,
        )
        if rows and rows[0].get("file_path"):
            return rows[0]["file_path"]
    return None


def tests_for_file(conn: Any, target_file: str) -> list[dict[str, str]]:
    """Test files that import ``target_file`` directly.

    Inferred heuristic: a test file is a File node whose role is `test`, and
    that has an IMPORTS edge into the target file. Returns
    ``[{"file", "role"}]`` (de-duplicated, order-preserving).
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in conn.find_neighbors(
        "IMPORTS",
        dst_key=target_file,
        return_src=["path", "role"],
        limit=_FANOUT_CAP,
    ):
        path = row.get("src_path")
        role = row.get("src_role") or ""
        if not path or path in seen:
            continue
        if not _is_test_role(role):
            continue
        seen.add(path)
        out.append({"file": path, "role": role})
    return out


def tests_calling_symbol(conn: Any, symbol: str) -> list[dict[str, str]]:
    """Test files that hold a function whose CALLS reach ``symbol``.

    Inferred heuristic: find Function nodes named ``symbol``, walk CALLS
    backward one hop, and keep callers that live in a `test`-role file.
    CALLS edges are same-file-scoped (per BUG-2) and name-matched, so this is
    a candidate set, not ground truth. Returns ``[{"file", "role"}]``.
    """
    target_ids = [
        r["id"]
        for r in conn.find_nodes(
            "Function", where={"name": symbol}, return_fields=["id"]
        )
    ]
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for tid in target_ids:
        for row in conn.find_neighbors(
            "CALLS",
            dst_key=tid,
            return_src=["id"],
            limit=_FANOUT_CAP,
        ):
            caller_id = row.get("src_id") or ""
            caller_file = caller_id.rsplit("::", 1)[0] if "::" in caller_id else ""
            if not caller_file or caller_file in seen:
                continue
            role, _ = file_role(conn, caller_file)
            if not _is_test_role(role):
                continue
            seen.add(caller_file)
            out.append({"file": caller_file, "role": role})
    return out


def tests_for(conn: Any, target: str) -> dict[str, Any]:
    """Full test-to-code mapping for one scope.

    Resolves ``target`` to a defining file, collects importing test files,
    and (when ``target`` is a symbol) test files whose calls reach it.
    Returns ``{target, target_file, tests: [{file, role}], count}``. When the
    target cannot be resolved, ``target_file`` is None and ``tests`` is empty.
    """
    target_file = resolve_target_file(conn, target)
    if target_file is None:
        return {"target": target, "target_file": None, "tests": [], "count": 0}

    seen: set[str] = set()
    tests: list[dict[str, str]] = []
    for t in tests_for_file(conn, target_file):
        if t["file"] in seen:
            continue
        seen.add(t["file"])
        tests.append(t)

    # When the argument named a symbol (not the file itself), also follow
    # the call graph from that symbol into test functions.
    if target != target_file:
        for t in tests_calling_symbol(conn, target):
            if t["file"] in seen:
                continue
            seen.add(t["file"])
            tests.append(t)

    return {
        "target": target,
        "target_file": target_file,
        "tests": tests,
        "count": len(tests),
    }


def untested_files(
    conn: Any,
    role: str = "",
    layer: str = "",
    cap: int = 200,
) -> tuple[list[dict[str, str]], bool]:
    """Non-test source files that no test file imports.

    Walks every File node, skips test / doc files, applies the optional
    role / layer filter, and keeps those with no `test`-role importer.
    Returns ``(rows, truncated)`` where each row is ``{file, role, layer}``.
    """
    where: dict[str, Any] = {}
    if role:
        where["role"] = role
    if layer:
        where["layer"] = layer

    files = conn.find_nodes(
        "File",
        where=where or None,
        return_fields=["path", "role", "layer"],
        order_by=["path"],
    )

    out: list[dict[str, str]] = []
    truncated = False
    for f in files:
        path = f.get("path")
        frole = f.get("role") or ""
        flayer = f.get("layer") or ""
        if not path:
            continue
        # Never report test or doc files as "untested".
        if frole in (TEST_ROLE, "doc"):
            continue
        if tests_for_file(conn, path):
            continue
        out.append({"file": path, "role": frole, "layer": flayer})
        if len(out) >= cap:
            truncated = True
            break
    return out, truncated


def reverse_import_bfs(
    conn: Any,
    start_files: list[str],
    max_depth: int = 3,
) -> tuple[list[str], bool]:
    """Bounded reverse BFS over IMPORTS: every file that transitively imports
    any of ``start_files`` within ``max_depth`` hops.

    Returns ``(ordered_file_paths, truncated)``. ``start_files`` themselves are
    not included in the result. Caps both the per-node fan-out and the total
    result size so a hub file cannot blow up the walk.
    """
    seen: set[str] = set(start_files)
    frontier = list(start_files)
    ordered: list[str] = []
    truncated = False
    depth = 0
    while frontier and depth < max(1, int(max_depth)):
        depth += 1
        nxt: list[str] = []
        for key in frontier:
            rows = conn.find_neighbors(
                "IMPORTS",
                dst_key=key,
                return_src=["path"],
                limit=_FANOUT_CAP,
            )
            if len(rows) >= _FANOUT_CAP:
                truncated = True
            for r in rows:
                src = r.get("src_path")
                if not src or src in seen:
                    continue
                seen.add(src)
                ordered.append(src)
                nxt.append(src)
                if len(ordered) >= _REVERSE_CAP:
                    return ordered, True
        frontier = nxt
    return ordered, truncated


def symbols_in_file(conn: Any, file_path: str) -> list[dict[str, str]]:
    """Functions and classes defined in ``file_path``.

    Returns ``[{name, kind, lines}]`` ordered by start line. Used by the
    impact command to report which symbols actually changed in a diff.
    """
    out: list[dict[str, str]] = []
    for label, kind in (("Function", "function"), ("Class", "class")):
        for s in conn.find_nodes(
            label,
            where={"file_path": file_path},
            return_fields=["name", "start_line", "end_line"],
            order_by=["start_line"],
        ):
            out.append(
                {
                    "name": s.get("name", ""),
                    "kind": kind,
                    "lines": f"{s.get('start_line', '')}-{s.get('end_line', '')}",
                }
            )
    return out


def endpoints_in_files(conn: Any, files: list[str]) -> list[dict[str, str]]:
    """Endpoints declared (DEFINES_ENDPOINT) in any of ``files``.

    Returns ``[{file, method, path}]``, de-duplicated.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for fp in files:
        for e in conn.find_neighbors(
            "DEFINES_ENDPOINT",
            src_key=fp,
            return_dst=["method", "path"],
        ):
            method = e.get("dst_method", "") or ""
            path = e.get("dst_path", "") or ""
            key = (fp, method, path)
            if key in seen:
                continue
            seen.add(key)
            out.append({"file": fp, "method": method, "path": path})
    return out
