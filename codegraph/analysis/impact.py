# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Pure GraphDB-protocol helpers shared by the test-mapping MCP
#              tools (tests_for / untested) and the `cgh impact` CI command.
#              Computes test-to-code mapping on the fly from IMPORTS + CALLS
#              edges, the imports made inside function bodies and File.role,
#              with no new edge type, plus bounded reverse walks over imports
#              and CALLS for blast radius. Backend-neutral: every call goes
#              through the GraphDB protocol, no raw SQL.
#              build_impact_report assembles the full `cgh impact` payload
#              for both the CLI and the impact_report MCP tool.

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Hard caps so a pathological graph never produces an unbounded result.
TEST_ROLE = "test"
_FANOUT_CAP = 500
_REVERSE_CAP = 300
# Functions a reverse CALLS walk may visit before it stops (truncated).
_CALLERS_CAP = 3000
# The name reference the indexer records for every repo file a file imports,
# at module level or inside a function body (indexer.CALLS_IMPORT).
_CALLS_IMPORT = "calls_import"


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


def _split_importers(conn: Any, target_file: str) -> tuple[list[str], list[str]]:
    """(module-level importers, function-body-only importers) of
    ``target_file``. IMPORTS edges carry the first; the imports made inside
    a function body are only recorded with the call sites (the file's
    "calls_import" references, which list module-level imports too)."""
    module = [
        p
        for p in dict.fromkeys(
            r.get("src_path") or ""
            for r in conn.find_neighbors(
                "IMPORTS", dst_key=target_file, return_src=["path"], limit=_FANOUT_CAP
            )
        )
        if p
    ]
    names = sorted({target_file, os.path.realpath(target_file)})
    known = set(module)
    local = sorted(
        {
            ref_file
            for kind, _from, ref_file, _name, _extra in conn.name_refs_into(
                names, target_file
            )
            if kind == _CALLS_IMPORT and ref_file and ref_file not in known
        }
    )
    return module, local


def importers_of(conn: Any, target_file: str) -> list[str]:
    """Files that import ``target_file``, at module level (IMPORTS) or
    inside a function body. Order-preserving, capped at _FANOUT_CAP."""
    module, local = _split_importers(conn, target_file)
    return (module + local)[:_FANOUT_CAP]


def tests_for_file(conn: Any, target_file: str) -> list[dict[str, str]]:
    """Test files that import ``target_file`` directly, at module level or
    inside a function body.

    Inferred heuristic: a test file is a File node whose role is `test`, and
    that imports the target file. Returns ``[{"file", "role"}]``
    (de-duplicated, order-preserving).
    """
    out: list[dict[str, str]] = []
    for path in importers_of(conn, target_file):
        role, _ = file_role(conn, path)
        if _is_test_role(role):
            out.append({"file": path, "role": role})
    return out


def tests_calling_symbol(conn: Any, symbol: str) -> list[dict[str, str]]:
    """Test files that hold a function whose CALLS reach ``symbol``.

    Inferred heuristic: find Function nodes named ``symbol``, walk CALLS
    backward one hop, and keep callers that live in a `test`-role file.
    CALLS edges are resolved from the call's shape and the caller's imports,
    not run, so this is a candidate set, not ground truth. Returns
    ``[{"file", "role"}]``.
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
    """Bounded reverse BFS over imports: every file that transitively imports
    any of ``start_files`` within ``max_depth`` hops. A file importing one
    only inside a function body is in, but the walk does not go on from it:
    a hub such as a conftest.py would pull in every test, and the callers of
    that function are found over CALLS (reverse_calls_bfs). A Terraform file
    counts as imported by the files whose blocks reference its blocks.

    Returns ``(ordered_file_paths, truncated)``. ``start_files`` themselves are
    not included in the result. Caps both the per-node fan-out and the total
    result size so a hub file cannot blow up the walk.
    """
    from codegraph.analysis.terraform import dependent_files as tf_dependent_files

    seen: set[str] = set(start_files)
    frontier = list(start_files)
    ordered: list[str] = []
    truncated = False
    depth = 0
    while frontier and depth < max(1, int(max_depth)):
        depth += 1
        nxt: list[str] = []
        for key in frontier:
            srcs, local = _split_importers(conn, key)
            if len(srcs) >= _FANOUT_CAP:
                truncated = True
            # A .tf file is "imported" by the files whose blocks reference
            # its blocks (Terraform has no import statement).
            srcs += tf_dependent_files(conn, key, limit=_FANOUT_CAP)
            leaves = set(local) - set(srcs)
            for src in srcs + local:
                if not src or src in seen:
                    continue
                seen.add(src)
                ordered.append(src)
                if src not in leaves:
                    nxt.append(src)
                if len(ordered) >= _REVERSE_CAP:
                    return ordered, True
        frontier = nxt
    return ordered, truncated


def reverse_calls_bfs(
    conn: Any,
    start_files: list[str],
    max_depth: int = 3,
) -> tuple[list[str], bool]:
    """Bounded reverse BFS over CALLS: the files holding a function that
    calls a function of ``start_files``, directly or through up to
    ``max_depth`` calls. Catches the callers no import edge shows (an import
    inside a function body, a call through an attribute of a known class).

    Returns ``(ordered_file_paths, truncated)``; ``start_files`` are not
    included. Caps the per-function fan-out, the functions visited and the
    files returned.
    """
    starts = set(start_files)
    frontier = sorted(
        {
            str(r["id"])
            for f in start_files
            for r in conn.find_nodes(
                "Function", where={"file_path": f}, return_fields=["id"]
            )
            if r.get("id")
        }
    )
    seen: set[str] = set(frontier)
    files: list[str] = []
    file_seen: set[str] = set()
    truncated = False
    for _ in range(max(1, int(max_depth))):
        nxt: list[str] = []
        for fid in frontier:
            rows = conn.find_neighbors(
                "CALLS",
                dst_key=fid,
                return_src=["id", "file_path"],
                limit=_FANOUT_CAP,
            )
            if len(rows) >= _FANOUT_CAP:
                truncated = True
            for row in rows:
                caller = row.get("src_id") or ""
                if not caller or caller in seen:
                    continue
                seen.add(caller)
                nxt.append(caller)
                path = row.get("src_file_path") or ""
                if path and path not in starts and path not in file_seen:
                    file_seen.add(path)
                    files.append(path)
                    if len(files) >= _REVERSE_CAP:
                        return files, True
            if len(seen) >= _CALLERS_CAP:
                return files, True
        if not nxt:
            break
        frontier = nxt
    return files, truncated


def symbols_in_file(conn: Any, file_path: str) -> list[dict[str, str]]:
    """Functions, classes and Terraform blocks defined in ``file_path``.

    Returns ``[{name, kind, lines}]`` ordered by start line. Used by the
    impact command to report which symbols actually changed in a diff.
    """
    from codegraph.analysis.terraform import blocks_in_file, label_of, tool_kind

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
    if file_path.endswith((".tf", ".tfvars")):
        for b in blocks_in_file(conn, file_path):
            out.append(
                {
                    "name": b.get("address") or "",
                    "kind": tool_kind(label_of(str(b["id"])), b.get("kind")),
                    "lines": f"{b.get('start_line', '')}-{b.get('end_line', '')}",
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


IMPACT_NOTE = (
    "Blast radius and tests are inferred from IMPORTS / CALLS edges "
    "(Terraform: block references), "
    "not a coverage run. Keep the index fresh with `cgh index` in CI."
)


def build_impact_report(conn: Any, root: str, changed_files: list[str]) -> dict:
    """The `cgh impact` report for a set of repo-relative changed files.

    Shared by the CLI (local read-only open) and the ``impact_report`` MCP
    tool (the owner's connection), so both paths return the same payload.
    All paths in the result are repo-relative.
    """
    root_path = Path(root).resolve()

    def _rel(p: str) -> str:
        try:
            return str(Path(p).resolve().relative_to(root_path))
        except (ValueError, OSError):
            return p

    # Changed files resolve to absolute File-node keys for graph lookups.
    abs_changed = [str(root_path / f) for f in changed_files]

    changed_symbols: list[dict] = []
    for abs_f, rel_f in zip(abs_changed, changed_files, strict=False):
        for sym in symbols_in_file(conn, abs_f):
            changed_symbols.append({"file": rel_f, **sym})

    # Blast radius: files that transitively import any changed file, then
    # the files whose functions reach a changed file's functions over CALLS.
    radius, radius_trunc = reverse_import_bfs(conn, abs_changed, max_depth=3)
    callers, callers_trunc = reverse_calls_bfs(conn, abs_changed, max_depth=3)
    in_radius = set(radius)
    radius += [p for p in callers if p not in in_radius]
    if len(radius) > _REVERSE_CAP:
        radius, callers_trunc = radius[:_REVERSE_CAP], True
    radius_trunc = radius_trunc or callers_trunc

    impacted: list[dict] = []
    by_role: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for abs_p in radius:
        role, layer = file_role(conn, abs_p)
        impacted.append({"file": _rel(abs_p), "role": role, "layer": layer})
        if role:
            by_role[role] = by_role.get(role, 0) + 1
        if layer:
            by_layer[layer] = by_layer.get(layer, 0) + 1

    # Endpoints declared in the changed files OR any impacted file.
    endpoints = [
        {"file": _rel(e["file"]), "method": e["method"], "path": e["path"]}
        for e in endpoints_in_files(conn, abs_changed + radius)
    ]

    # Tests to run: the test files that import a changed file (at module
    # level or in a function body), then the ones whose functions reach a
    # changed file's functions over CALLS.
    test_seen: set[str] = set()
    tests: list[dict] = []
    found = [t for abs_f in abs_changed for t in tests_for_file(conn, abs_f)]
    for path in callers:
        role, _ = file_role(conn, path)
        if _is_test_role(role):
            found.append({"file": path, "role": role})
    for t in found:
        rel_t = _rel(t["file"])
        if rel_t in test_seen:
            continue
        test_seen.add(rel_t)
        tests.append({"file": rel_t, "role": t["role"]})

    return {
        "since_changed": list(changed_files),
        "changed_symbols": changed_symbols,
        "impacted": impacted,
        "impacted_count": len(impacted),
        "impacted_by_role": by_role,
        "impacted_by_layer": by_layer,
        "endpoints": endpoints,
        "tests_to_run": tests,
        "truncated": radius_trunc,
        "note": IMPACT_NOTE,
    }
