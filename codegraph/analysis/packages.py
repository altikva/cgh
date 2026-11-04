# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Query side of the package manifests and MCP client configs.
#              collect(conn) reads one scope's Package and McpServer nodes,
#              its DEPENDS_ON edges and its pkg_dep references;
#              packages_payload(per_scope, ...) turns them into the answer
#              the packages MCP tool and `cgh packages` both print: each
#              package with its internal dependencies (a package declared in
#              the same scope), its external ones (name and spec only), the
#              packages that depend on it, the internal dependency cycles,
#              and the declared MCP servers.

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from codegraph.analysis.cycles import tarjan_scc

PKG_DEP = "pkg_dep"

_PKG_FIELDS = [
    "id",
    "name",
    "pkg_key",
    "ecosystem",
    "version",
    "file_path",
    "start_line",
    "workspaces",
    "workspace_root",
]
_MCP_FIELDS = [
    "id",
    "name",
    "file_path",
    "start_line",
    "transport",
    "command",
    "url",
    "arg_count",
    "env_keys",
    "header_keys",
]


def collect(conn) -> list[dict]:
    """One scope's raw package data, as a one-item list (the shape the
    federation helpers expect from a query)."""
    packages = conn.find_nodes("Package", return_fields=_PKG_FIELDS, order_by=["id"])
    edges = conn.find_neighbors(
        "DEPENDS_ON",
        return_src=["id"],
        return_dst=["id"],
        return_edge=["dep_group", "spec"],
    )
    deps = conn.name_refs_of_kind(PKG_DEP)
    servers = conn.find_nodes("McpServer", return_fields=_MCP_FIELDS, order_by=["id"])
    return [
        {
            "packages": packages,
            "edges": [
                (e["src_id"], e["dst_id"], e["edge_dep_group"], e["edge_spec"])
                for e in edges
            ],
            "deps": [tuple(d) for d in deps],
            "servers": servers,
        }
    ]


def _split(text: str | None) -> list[str]:
    return [p for p in (text or "").split(",") if p]


def _dep_parts(extra: str) -> tuple[str, str, str]:
    """(group, declared name, spec) out of a pkg_dep reference's extra."""
    group, name, spec = ([*extra.split("\t", 2), "", ""])[:3]
    return group, name, spec


def _label(pkg: dict, short_path: Callable[[str], str]) -> str:
    """A package's name, or its manifest's directory for an unnamed one."""
    if pkg.get("name"):
        return pkg["name"]
    return f"({short_path(os.path.dirname(pkg['file_path'])) or '.'})"


def _scope_report(
    scope: str,
    data: dict,
    short_path: Callable[[str], str],
) -> tuple[list[dict], list[list[str]], list[dict]]:
    pkgs = {p["id"]: p for p in data.get("packages") or []}
    labels = {pid: _label(p, short_path) for pid, p in pkgs.items()}
    internal: dict[str, list[dict]] = {pid: [] for pid in pkgs}
    dependents: dict[str, list[dict]] = {pid: [] for pid in pkgs}
    adj: dict[str, list[str]] = {pid: [] for pid in pkgs}
    for src, dst, group, spec in data.get("edges") or []:
        if src not in pkgs or dst not in pkgs:
            continue
        internal[src].append(
            {
                "name": labels[dst],
                "group": group,
                "spec": spec,
                "file": pkgs[dst]["file_path"],
            }
        )
        dependents[dst].append({"name": labels[src], "group": group, "spec": spec})
        if dst not in adj[src]:
            adj[src].append(dst)
    # A dependency is internal when its key names a package of this scope.
    declared = {p.get("pkg_key") for p in pkgs.values() if p.get("pkg_key")}
    external: dict[str, list[dict]] = {pid: [] for pid in pkgs}
    for from_id, key, extra in data.get("deps") or []:
        if from_id not in pkgs or key in declared:
            continue
        group, name, spec = _dep_parts(extra)
        external[from_id].append({"name": name, "group": group, "spec": spec})

    rows = []
    for pid, pkg in pkgs.items():
        row = {
            "name": labels[pid],
            "ecosystem": pkg.get("ecosystem") or "",
            "version": pkg.get("version") or "",
            "file": pkg["file_path"],
            "line": pkg.get("start_line") or 1,
            "internal": sorted(internal[pid], key=lambda d: (d["name"], d["group"])),
            "external": sorted(external[pid], key=lambda d: (d["name"], d["group"])),
            "dependents": sorted(
                dependents[pid], key=lambda d: (d["name"], d["group"])
            ),
            "scope": scope,
        }
        if pkg.get("workspaces"):
            row["workspaces"] = _split(pkg["workspaces"])
        if pkg.get("workspace_root"):
            row["workspace_root"] = pkg["workspace_root"]
        rows.append(row)

    cycles = []
    for comp in tarjan_scc(adj):
        if len(comp) > 1 or comp[0] in adj.get(comp[0], []):
            cycles.append(sorted(labels[pid] for pid in comp))

    servers = []
    for srv in data.get("servers") or []:
        entry = {
            "name": srv.get("name") or "",
            "transport": srv.get("transport") or "",
            "file": srv["file_path"],
            "line": srv.get("start_line") or 1,
            "scope": scope,
        }
        for key, value in (
            ("command", srv.get("command")),
            ("url", srv.get("url")),
            ("arg_count", srv.get("arg_count")),
            ("env_keys", _split(srv.get("env_keys"))),
            ("header_keys", _split(srv.get("header_keys"))),
        ):
            if value:
                entry[key] = value
        servers.append(entry)
    return rows, cycles, servers


def packages_payload(
    per_scope: list[tuple[str, list]],
    name: str = "",
    include_external: bool = False,
    limit: int = 200,
    short_path: Callable[[str], str] = lambda p: p,
) -> dict[str, Any]:
    """The packages answer over every scope.

    Without ``name``: every package with its internal dependencies and
    dependents, external dependencies as a count unless
    ``include_external``. With ``name`` (package name, exact first, else a
    substring): only the matching packages, external dependencies listed.
    Cycles and MCP servers are always reported in full.
    """
    rows: list[dict] = []
    cycles: list[dict] = []
    servers: list[dict] = []
    for scope, payload in per_scope:
        for data in payload or []:
            r, c, s = _scope_report(scope, data, short_path)
            rows += r
            cycles += [{"scope": scope, "packages": cyc} for cyc in c]
            servers += s

    for row in rows:
        row["file"] = short_path(row["file"])
        for dep in row["internal"]:
            dep["file"] = short_path(dep["file"])
    for srv in servers:
        srv["file"] = short_path(srv["file"])

    if name:
        exact = [r for r in rows if r["name"].lower() == name.lower()]
        rows = exact or [r for r in rows if name.lower() in r["name"].lower()]
        include_external = True
    if not include_external:
        for row in rows:
            row["external_count"] = len(row.pop("external"))

    rows.sort(key=lambda r: (r["scope"] != "parent", r["scope"], r["name"]))
    total = len(rows)
    limit = max(1, int(limit))
    out: dict[str, Any] = {
        "total": total,
        "internal_edges": sum(len(r["internal"]) for r in rows),
        "packages": rows[:limit],
        "truncated": total > limit,
        "cycles": cycles,
        "mcp_servers": servers,
    }
    if name:
        out["query"] = name
    return out


__all__ = ["collect", "packages_payload"]
