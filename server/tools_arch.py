# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP architecture-awareness tools — architecture_overview,
#              domain_map, endpoints. Designed to be the FIRST tools Claude
#              calls for new-feature / code-exploration tasks.

from __future__ import annotations

import fnmatch
import json
from collections import defaultdict

from codegraph.core.utils import rows as _rows
from codegraph.core.utils import short_path as _short_path


def register(mcp) -> None:
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _logged_tool

    @mcp.tool()
    @_logged_tool
    def architecture_overview(max_files_per_role: int = 10) -> str:
        """
        Compact map of the codebase grouped by architectural layer + role.

        CALL THIS FIRST when the user asks a broad question like
        "how does X work", "where should I add Y", "explain the structure".
        Cheaper than reading files: returns at most ~200 lines of JSON.

        Output shape:
          {
            "presentation": {
              "router":    [{path, module_doc, symbols}, ...],
              "component": [...],
              ...
            },
            "application": { "handler": [...], ... },
            "domain":      { "model": [...], "schema": [...] },
            "infra":       { "provider": [...], ... },
            "test":        { "test": [...] },
            "doc":         { "doc": [...] }
          }
        """
        conn = _get_conn()
        try:
            r = conn.execute(
                "MATCH (f:File) RETURN f.path, f.lang, f.role, f.layer, f.module_doc ORDER BY f.layer, f.role, f.path"
            )
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)})

        by_layer: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for row in _rows(r):
            layer = row.get("f.layer") or "other"
            role = row.get("f.role") or "other"
            entry = {
                "path": _short_path(row["f.path"], _srv._root),
                "lang": row.get("f.lang"),
                "module_doc": (row.get("f.module_doc") or "")[:180],
            }
            by_layer[layer][role].append(entry)

        # Sort within each role, cap list size
        for layer_dict in by_layer.values():
            for role_name, files in layer_dict.items():
                files.sort(key=lambda e: e["path"])
                if len(files) > max_files_per_role:
                    layer_dict[role_name] = files[:max_files_per_role] + [
                        {"path": f"... {len(files) - max_files_per_role} more"}
                    ]

        from codegraph.roles import LAYER_ORDER

        ordered = {lyr: dict(by_layer[lyr]) for lyr in LAYER_ORDER if lyr in by_layer}
        # Include any unknown layers at the end
        for lyr, val in by_layer.items():
            if lyr not in ordered:
                ordered[lyr] = dict(val)

        return json.dumps(ordered, indent=2)

    @mcp.tool()
    @_logged_tool
    def domain_map(keyword: str, limit_per_role: int = 8) -> str:
        """
        All files related to a domain/feature keyword, grouped by role.

        Use when the user names a concept ("stats", "donor merge", "Cerfa")
        to find every handler/router/model/etc touching it — faster than
        grep because it respects the role taxonomy.

        Matches against file path, role, and module_doc (case-insensitive).
        """
        if not keyword.strip():
            return json.dumps({"error": "keyword is required"})

        conn = _get_conn()
        k = keyword.strip().lower()
        try:
            r = conn.execute("MATCH (f:File) RETURN f.path, f.lang, f.role, f.layer, f.module_doc")
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)})

        hits_by_role: dict[str, list[dict]] = defaultdict(list)
        for row in _rows(r):
            p = row["f.path"]
            doc = row.get("f.module_doc") or ""
            role = row.get("f.role") or "other"
            if k in p.lower() or k in doc.lower() or k == (role or "").lower():
                hits_by_role[role].append(
                    {
                        "path": _short_path(p, _srv._root),
                        "layer": row.get("f.layer"),
                        "module_doc": doc[:160],
                    }
                )

        # Cap per role
        for role_name, files in hits_by_role.items():
            files.sort(key=lambda e: e["path"])
            if len(files) > limit_per_role:
                hits_by_role[role_name] = files[:limit_per_role] + [{"path": f"... {len(files) - limit_per_role} more"}]

        total = sum(len(v) for v in hits_by_role.values() if not any("more" in str(e.get("path", "")) for e in v))
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
        List HTTP endpoints in the codebase.

        Optional filters:
          path_pattern — glob like "*/donations*" or "/api/stats/*"
          method       — GET / POST / PUT / PATCH / DELETE (case-insensitive)

        Returns: [{method, path, framework, file, line, handler}] grouped by
        framework. Includes both FastAPI decorators and Nuxt server/api
        routes, so cross-repo questions ("does the frontend call this Python
        route?") become navigable.
        """
        conn = _get_conn()
        try:
            r = conn.execute(
                "MATCH (e:Endpoint) "
                "OPTIONAL MATCH (e)-[:IMPLEMENTED_BY]->(fn:Function) "
                "RETURN e.method, e.path, e.framework, e.file_path, e.start_line, fn.name "
                "ORDER BY e.path, e.method"
            )
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)})

        data = _rows(r)
        method_filter = method.strip().upper() or None

        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in data:
            url = row.get("e.path") or ""
            mth = row.get("e.method") or ""
            if method_filter and mth != method_filter:
                continue
            if path_pattern:
                if not fnmatch.fnmatch(url, path_pattern):
                    continue
            grouped[row.get("e.framework") or "unknown"].append(
                {
                    "method": mth,
                    "path": url,
                    "handler": row.get("fn.name"),
                    "file": _short_path(row["e.file_path"], _srv._root),
                    "line": row.get("e.start_line"),
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
