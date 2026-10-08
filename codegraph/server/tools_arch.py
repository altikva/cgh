# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP architecture-awareness tools: architecture_overview,
#              domain_map, endpoints. Designed to be the FIRST tools Claude
#              calls for new-feature / code-exploration tasks.

from __future__ import annotations

import fnmatch
import json
from collections import defaultdict

from codegraph.core.utils import short_path as _short_path


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.analysis.federation import federate_scoped
    from codegraph.server import _get_conn, _logged_tool

    def _query_each(query_fn):
        """Run query_fn(conn) on parent + each child; return [(scope, payload), …].
        See federation.federate_scoped (warnings dropped here, callers don't use them)."""
        scoped, _warnings = federate_scoped(_get_conn, _srv._root, query_fn)
        return scoped

    @mcp.tool()
    @_logged_tool
    def architecture_overview(max_files_per_role: int = 10) -> str:
        """
        How is this codebase organised?
        Call first on a broad question ("how does X work", "where do I add
        Y"). Returns files grouped by layer (presentation, application,
        domain, infra, test, doc) then role, each with path, module_doc and
        symbols, in about 200 lines of JSON. One feature: domain_map. A
        concrete task: context_for_task.
        """

        def query(conn):
            try:
                return conn.find_nodes(
                    "File",
                    return_fields=["path", "lang", "role", "layer", "module_doc"],
                    order_by=["layer", "role", "path"],
                )
            except RuntimeError:
                return []

        per_scope = _query_each(query)
        scopes_out: dict[str, dict] = {}

        from codegraph.analysis.roles import LAYER_ORDER

        for scope, rows in per_scope:
            by_layer: dict[str, dict[str, list[dict]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for row in rows:
                layer = row.get("layer") or "other"
                role = row.get("role") or "other"
                by_layer[layer][role].append(
                    {
                        "path": _short_path(row["path"], _srv._root),
                        "lang": row.get("lang"),
                        "module_doc": (row.get("module_doc") or "")[:180],
                    }
                )
            for layer_dict in by_layer.values():
                for role_name, files in layer_dict.items():
                    files.sort(key=lambda e: e["path"])
                    if len(files) > max_files_per_role:
                        layer_dict[role_name] = [
                            *files[:max_files_per_role],
                            {"path": f"... {len(files) - max_files_per_role} more"},
                        ]
            ordered = {
                lyr: dict(by_layer[lyr]) for lyr in LAYER_ORDER if lyr in by_layer
            }
            for lyr, val in by_layer.items():
                if lyr not in ordered:
                    ordered[lyr] = dict(val)
            scopes_out[scope] = ordered

        # If only the parent scope is present, keep the legacy flat shape.
        if list(scopes_out.keys()) == ["parent"]:
            return json.dumps(scopes_out["parent"], indent=2)
        return json.dumps({"by_scope": scopes_out}, indent=2)

    @mcp.tool()
    @_logged_tool
    def domain_map(keyword: str, limit_per_role: int = 8) -> str:
        """
        Which files touch this feature?
        Give a feature keyword ("stats", "donor merge"); returns the matching
        files grouped by role (router, handler, model...), matched on path,
        role and module_doc. Wider than search_symbols (names only), cheaper
        than pattern_search over the repo.
        """
        if not keyword.strip():
            return json.dumps({"error": "keyword is required"})

        k = keyword.strip().lower()

        def query(conn):
            try:
                files = conn.find_nodes(
                    "File",
                    return_fields=["path", "lang", "role", "layer", "module_doc"],
                )
            except RuntimeError:
                return []
            out = []
            for row in files:
                p = row["path"]
                doc = row.get("module_doc") or ""
                role = row.get("role") or "other"
                if k in p.lower() or k in doc.lower() or k == (role or "").lower():
                    out.append(
                        {
                            "path": _short_path(p, _srv._root),
                            "layer": row.get("layer"),
                            "module_doc": doc[:160],
                            "role": role,
                        }
                    )
            return out

        per_scope = _query_each(query)
        hits_by_role: dict[str, list[dict]] = defaultdict(list)
        for scope, rows in per_scope:
            for row in rows:
                hits_by_role[row.pop("role")].append({**row, "scope": scope})

        for role_name, files in hits_by_role.items():
            files.sort(key=lambda e: e["path"])
            if len(files) > limit_per_role:
                hits_by_role[role_name] = [
                    *files[:limit_per_role],
                    {"path": f"... {len(files) - limit_per_role} more"},
                ]

        total = sum(
            len(v)
            for v in hits_by_role.values()
            if not any("more" in str(e.get("path", "")) for e in v)
        )
        return json.dumps(
            {
                "keyword": keyword,
                "total": total,
                "files_by_role": dict(hits_by_role),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def endpoints(path_pattern: str = "", method: str = "") -> str:
        """
        Which HTTP routes exist, and which handler serves each?
        Lists FastAPI and Nuxt server/api routes as {method, path,
        framework, file, line, handler}. Filter with path_pattern (a glob
        such as "/api/stats/*") and method. Use for API questions instead of
        grepping decorators.
        """
        method_filter = method.strip().upper() or None

        def query(conn):
            try:
                eps = conn.find_nodes(
                    "Endpoint",
                    return_fields=[
                        "id",
                        "method",
                        "path",
                        "framework",
                        "file_path",
                        "start_line",
                    ],
                    order_by=["path", "method"],
                )
            except RuntimeError:
                return []
            # OPTIONAL MATCH equivalent: for each endpoint, look up handler
            # name via the IMPLEMENTED_BY edge. None when there's no handler.
            out = []
            for ep in eps:
                handlers = conn.find_neighbors(
                    "IMPLEMENTED_BY",
                    src_key=ep["id"],
                    return_dst=["name"],
                )
                ep["handler_name"] = handlers[0]["dst_name"] if handlers else None
                out.append(ep)
            return out

        per_scope = _query_each(query)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for scope, rows in per_scope:
            for row in rows:
                url = row.get("path") or ""
                mth = row.get("method") or ""
                if method_filter and mth != method_filter:
                    continue
                if path_pattern and not fnmatch.fnmatch(url, path_pattern):
                    continue
                grouped[row.get("framework") or "unknown"].append(
                    {
                        "scope": scope,
                        "method": mth,
                        "path": url,
                        "handler": row.get("handler_name"),
                        "file": _short_path(row["file_path"], _srv._root),
                        "line": row.get("start_line"),
                    }
                )

        total = sum(len(v) for v in grouped.values())
        return json.dumps(
            {
                "total": total,
                "by_framework": dict(grouped),
            },
            indent=2,
        )
