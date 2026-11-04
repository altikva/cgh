# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP architecture-awareness tools: architecture_overview,
#              domain_map, endpoints, packages. Designed to be the FIRST tools
#              Claude calls for new-feature / code-exploration tasks.

from __future__ import annotations

from collections import defaultdict

from codegraph.core.utils import short_path as _short_path
from codegraph.server._budget import dumps_answer


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
            "doc":         { "doc": [...] },
            "subsystems":  [{name, size, central, scope?}, ...]
          }

        "subsystems" lists the biggest communities of connected files (see
        the communities tool for their members, symbols and links). In a
        federated project the layers sit under "by_scope" and each
        subsystem carries its scope.
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

        subsystems = _subsystems()
        # If only the parent scope is present, keep the legacy flat shape.
        if list(scopes_out.keys()) == ["parent"]:
            flat = scopes_out["parent"]
            if subsystems:
                flat["subsystems"] = [
                    {k: v for k, v in s.items() if k != "scope"} for s in subsystems
                ]
            return dumps_answer(flat)
        out: dict = {"by_scope": scopes_out}
        if subsystems:
            out["subsystems"] = subsystems
        return dumps_answer(out)

    def _subsystems(per_scope_cap: int = 8) -> list[dict]:
        """The biggest communities of each scope, for architecture_overview.
        Best effort: the overview answers without them on any failure."""
        try:
            from codegraph.analysis.communities import communities_for

            per_scope, _warnings = federate_scoped(
                _get_conn, _srv._root, lambda conn: communities_for(conn)
            )
        except Exception:
            return []
        rows: list[dict] = []
        for scope, result in per_scope:
            kept = [c for c in result.communities if c.size > 1][:per_scope_cap]
            rows.extend(
                {
                    "name": c.name,
                    "size": c.size,
                    "central": _short_path(c.files[0], _srv._root),
                    "scope": scope,
                }
                for c in kept
            )
        return rows

    @mcp.tool()
    @_logged_tool
    def domain_map(keyword: str, limit_per_role: int = 8) -> str:
        """
        All files related to a domain/feature keyword, grouped by role.

        Use when the user names a concept ("stats", "donor merge", "Cerfa")
        to find every handler/router/model/etc touching it, faster than
        grep because it respects the role taxonomy.

        Matches against file path, role, and module_doc (case-insensitive).
        """
        if not keyword.strip():
            return dumps_answer({"error": "keyword is required"})

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
        return dumps_answer(
            {
                "keyword": keyword,
                "total": total,
                "files_by_role": dict(hits_by_role),
            }
        )

    @mcp.tool()
    @_logged_tool
    def communities(
        resolution: float = 1.0, min_size: int = 2, max_communities: int = 30
    ) -> str:
        """
        Subsystems detected from how the code actually connects, not from
        directory names: IMPORTS and CALLS folded into a weighted file graph,
        clustered by modularity (Louvain), each cluster named from its own
        files (dominant directory, recurring file-name words).

        Use it to learn which files form one unit before changing them, or
        to see how a codebase splits when its directories do not say.

        Args:
          resolution: above 1 gives more, smaller communities; below 1 fewer,
            larger ones (default 1.0).
          min_size: leave out communities with fewer files (default 2).
          max_communities: cap per scope (default 30), biggest first.

        Returns JSON {communities: [{scope, id, name, size, central, dirs,
        files, files_more?, top_symbols: [{name, file, line, callers}],
        cohesion, links: [{id, name, weight}]}], shared_utilities: [{scope,
        file, degree, communities}], scopes: [{scope, files_clustered, edges,
        communities, modularity, ...}], count, truncated, note}. cohesion is
        the share of a community's edge weight that stays inside it. Shared
        utilities are files so widely used they were left out of clustering
        (they would glue everything together). Test files are left out.
        Same graph, same answer; cached until the index changes.
        """
        from codegraph.analysis.communities import (
            communities_for,
            communities_payload,
        )

        per_scope, warnings = federate_scoped(
            _get_conn, _srv._root, lambda conn: communities_for(conn, resolution)
        )
        payload = communities_payload(
            per_scope,
            lambda p: _short_path(p, _srv._root),
            resolution=resolution,
            min_size=min_size,
            max_communities=max_communities,
        )
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return dumps_answer(payload)

    @mcp.tool()
    @_logged_tool
    def architecture_report(max_items: int = 10, resolution: float = 1.0) -> str:
        """
        One Markdown page on how the codebase is put together, built from
        the graph with no LLM: the subsystems (see communities) with names
        and sizes, shared utilities, pivot files (change-risk hotspots when
        git history is there, import in-degree otherwise, each marked when no
        test imports it), bridges (the only link between two subsystems,
        files reaching many of them), unexpected links (layer inversions,
        calls with no import behind them), import cycles, and a short list
        of questions worth asking, generated by fixed rules from those
        findings.

        Use it for a first look at an unfamiliar repo, before a refactor, or
        to prepare an architecture review. Same index, same report.

        Args:
          max_items: rows per section (default 10).
          resolution: passed to the community detection (default 1.0).

        Returns JSON {markdown, questions, scopes: [{scope, subsystems,
        pivots, untested_pivots, sole_links, bridge_files,
        layer_inversions, calls_without_import, import_cycles, ...}],
        partial?, warnings?}. Federated: one section per scope.
        """
        from codegraph.analysis.arch_report import build_report

        try:
            conn = _get_conn()
        except Exception:
            conn = None
        payload = build_report(
            conn,
            _srv._root,
            lambda p: _short_path(p, _srv._root),
            resolution=resolution,
            max_items=max(1, int(max_items)),
        )
        return dumps_answer(payload)

    @mcp.tool()
    @_logged_tool
    def endpoints(
        path_pattern: str = "", method: str = "", include_tests: bool = False
    ) -> str:
        """
        List HTTP endpoints in the codebase.

        Optional filters:
          path_pattern, a path, concrete URL or glob:
            "/v1/donations/{id}/cancel", "/v1/donations/123/cancel",
            "*/donations*". Matched against each route's full paths (router
            prefixes and include_router / blueprint prefixes composed), or
            its local path when it has none; parameter names are ignored, a
            {p:path} catch-all takes the rest of the path, on an exact match
            only, and ranks after a more specific route. A path starting with "/" that matches
            nothing falls back to the routes it is a suffix of
            ("/donations/{id}/cancel"), flagged match: "suffix".
          method, GET / POST / PUT / PATCH / DELETE (case-insensitive);
            routes declared for ANY method always pass
          include_tests, also list routes declared in test files (left out
            by default, counted in tests_excluded)

        Returns: {total, tests_excluded, by_framework: {framework: [{method,
        path, full_paths, handler, file, line, scope, match?,
        full_path_partial?, test?}]}}. full_paths is empty, and
        full_path_partial true, when a prefix is not a string literal.
        Includes FastAPI / Flask decorators and Nuxt server/api routes, so
        cross-repo questions ("does the frontend call this Python route?")
        become navigable.
        """
        from codegraph.analysis.endpoint_query import list_endpoints, select_endpoints

        per_scope, warnings = federate_scoped(_get_conn, _srv._root, list_endpoints)
        payload = select_endpoints(
            per_scope,
            path_pattern=path_pattern,
            method=method,
            include_tests=include_tests,
            short_path=lambda p: _short_path(p, _srv._root),
        )
        if warnings:
            payload["warnings"] = warnings
        return dumps_answer(payload)

    @mcp.tool()
    @_logged_tool
    def packages(
        name: str = "", include_external: bool = False, limit: int = 200
    ) -> str:
        """
        Packages declared by the repo's manifests (package.json, including
        npm / pnpm / yarn workspaces, and pyproject.toml, including uv
        workspace members), how they depend on each other, and the MCP
        servers its client configs declare (.mcp.json, .cursor/mcp.json,
        .vscode/mcp.json).

        A dependency is internal when it names a package declared in the
        same repo (a monorepo sibling), external otherwise (name and version
        spec only, nothing is fetched).

        Args:
          name: a package name (exact first, else a substring) to show only
            that package, with its external dependencies and the packages
            that depend on it ("who depends on X").
          include_external: list every external dependency instead of a
            count, when no name is given.
          limit: max packages returned (default 200).

        Returns: {total, internal_edges, truncated, packages: [{name,
        ecosystem, version, file, line, internal: [{name, group, spec,
        file}], external | external_count, dependents: [{name, group,
        spec}], workspaces?, workspace_root?, scope}], cycles: [{scope,
        packages}], mcp_servers: [{name, transport, command?, url?, arg_count?,
        env_keys?, header_keys?, file, line, scope}]}. MCP servers carry the
        names of their env vars and headers and the number of arguments,
        never their values.
        """
        from codegraph.analysis.packages import collect, packages_payload

        per_scope, warnings = federate_scoped(_get_conn, _srv._root, collect)
        payload = packages_payload(
            per_scope,
            name=name,
            include_external=include_external,
            limit=limit,
            short_path=lambda p: _short_path(p, _srv._root),
        )
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return dumps_answer(payload)
