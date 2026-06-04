# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP meta tools: fts_search, dead_code, context_for_task, call_stats.

from __future__ import annotations

import json


def register(mcp) -> None:
    """Register meta tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _get_fts, _logged_tool

    @mcp.tool()
    @_logged_tool
    def fts_search(query: str, limit: int = 15, kind: str = "") -> str:
        """
        Full-text search over symbol names AND docstrings using BM25 ranking.
        Federated across subrepos. Each result tagged with `scope`. The
        `limit` is applied per scope; scores are NOT renormalized across
        repos (BM25 is corpus-relative).
        """
        from codegraph.analysis.federation import for_each_child_fts
        from codegraph.core.fts import fts_search as _fts

        def _result_to_dict(r, scope):
            return {
                "scope": scope,
                "kind": r.kind,
                "name": r.name,
                "file": r.file_path,
                "lines": f"{r.start_line}-{r.end_line}" if r.end_line else str(r.start_line),
                "doc": r.docstring,
                "score": round(r.score, 4),
            }

        all_results: list[dict] = []
        warnings: list[dict] = []

        # Parent — use cached conn from server
        try:
            parent_results = _fts(
                _get_fts(),
                query,
                limit=limit,
                kind_filter=kind if kind else None,
            )
            all_results.extend(_result_to_dict(r, "parent") for r in parent_results)
        except Exception as exc:
            warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})

        # Children — fresh RO conns
        if _srv._root is not None:
            for scoped in for_each_child_fts(
                _srv._root,
                lambda c, _r: _fts(c, query, limit=limit, kind_filter=kind if kind else None),
            ):
                if scoped.error:
                    warnings.append({"scope": scoped.scope, "error": scoped.error})
                    continue
                all_results.extend(_result_to_dict(r, scoped.scope) for r in scoped.payload or [])

        # Sort across federation by score (BM25 returns negative — higher abs is better)
        all_results.sort(key=lambda x: -x["score"])

        out: dict = {"query": query, "results": all_results}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def find_dead_code(
        file_path: str = "",
        include_private: bool = False,
    ) -> str:
        """
        Find potentially unused functions, classes, and Terraform resources.
        A symbol is flagged when no CALLS / INHERITS / TF_DEPENDS edge points
        to it and it is not a known entry-point name.

        FEDERATION CAVEAT: results are computed per scope (parent + each
        subrepo). A symbol "dead" in subrepo X may actually be called from
        the parent or another subrepo — we don't infer cross-repo edges.
        Treat the results as a per-scope candidate list, not a hard verdict.
        """
        from codegraph.analysis.dead_code import find_dead_code as _find_dead
        from codegraph.analysis.federation import for_each_child_kuzu

        all_dead: list[dict] = []
        warnings: list[dict] = []

        try:
            parent = _find_dead(
                _get_conn(),
                include_private=include_private,
                file_filter=file_path if file_path else None,
            )
            for d in parent:
                all_dead.append(
                    {
                        "scope": "parent",
                        "kind": d.kind,
                        "name": d.name,
                        "file": d.file_path,
                        "lines": f"{d.start_line}-{d.end_line}",
                        "reason": d.reason,
                    }
                )
        except Exception as exc:
            warnings.append({"scope": "parent", "error": f"{type(exc).__name__}: {exc}"})

        if _srv._root is not None:
            for scoped in for_each_child_kuzu(
                _srv._root,
                lambda c, _r: _find_dead(
                    c,
                    include_private=include_private,
                    file_filter=file_path if file_path else None,
                ),
            ):
                if scoped.error:
                    warnings.append({"scope": scoped.scope, "error": scoped.error})
                    continue
                for d in scoped.payload or []:
                    all_dead.append(
                        {
                            "scope": scoped.scope,
                            "kind": d.kind,
                            "name": d.name,
                            "file": d.file_path,
                            "lines": f"{d.start_line}-{d.end_line}",
                            "reason": d.reason,
                        }
                    )

        out: dict = {
            "dead_symbols": all_dead,
            "count": len(all_dead),
            "note": "per-scope analysis — a symbol may be live via cross-repo callers",
        }
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return json.dumps(out, indent=2)

    @mcp.tool()
    @_logged_tool
    def context_for_task(
        task: str,
        max_nodes: int = 15,
        session_id: str = "",
        include_shown: bool = False,
    ) -> str:
        """
        THE FIRST TOOL TO CALL for any coding task.
        Given a natural-language task description, builds a compact, ranked context
        block containing the most relevant symbols, their docstrings, and their
        graph relationships — plus Claude Code memory entries and related plan
        files — WITHOUT reading any files.

        Use this before any file reads. It will cut exploration tokens by 60-90%.

        Args:
            task: description of what you need to do
                  e.g. "fix the authentication token validation logic"
                       "add a new GCS bucket resource"
                       "refactor the DataLoader class"
            max_nodes: max symbols to include (default 15)
            session_id: optional — when passed, already-surfaced entities
                  from previous calls in THIS session are hidden. Pass the
                  same id across calls to avoid re-serving the same nodes.
            include_shown: if true, ignore session dedup and return
                  everything. Useful to review what was previously served.

        Returns structured markdown context + hit counts.
        """
        from codegraph.state.call_log import filter_unseen, record_mentions
        from codegraph.analysis.context_builder import context_for_task as _ctx
        from codegraph.analysis.context_builder import render_context_markdown

        ctx = _ctx(
            task=task,
            kuzu_conn=_get_conn(),
            fts_conn=_get_fts(),
            max_nodes=max_nodes,
        )

        # Session-scoped dedup
        if session_id and not include_shown:
            from codegraph.state.activity import log as _activity_log

            served_nodes = [("symbol", f"{n.file_path}:{n.start_line}") for n in ctx.nodes]
            served_mem = [("memory", m.path) for m in ctx.memory_docs]
            served_plans = [("plan", p.path) for p in ctx.plan_docs]
            served_know = [("knowledge", str(k.id)) for k in ctx.knowledge_docs]
            all_entities = served_nodes + served_mem + served_plans + served_know

            unseen = set(filter_unseen(session_id, all_entities, repo_root=_srv._root))
            before = len(ctx.nodes) + len(ctx.memory_docs) + len(ctx.plan_docs)

            ctx.nodes = [n for n in ctx.nodes if ("symbol", f"{n.file_path}:{n.start_line}") in unseen]
            ctx.memory_docs = [m for m in ctx.memory_docs if ("memory", m.path) in unseen]
            ctx.plan_docs = [p for p in ctx.plan_docs if ("plan", p.path) in unseen]
            ctx.knowledge_docs = [k for k in ctx.knowledge_docs if ("knowledge", str(k.id)) in unseen]

            after = len(ctx.nodes) + len(ctx.memory_docs) + len(ctx.plan_docs) + len(ctx.knowledge_docs)
            if before != after:
                try:
                    _activity_log(_srv._root, "session_dedup", f"{session_id} hid {before - after}")
                except Exception:
                    pass

            # Record what we're about to serve
            now_served = (
                [("symbol", f"{n.file_path}:{n.start_line}") for n in ctx.nodes]
                + [("memory", m.path) for m in ctx.memory_docs]
                + [("plan", p.path) for p in ctx.plan_docs]
                + [("knowledge", str(k.id)) for k in ctx.knowledge_docs]
            )
            if now_served:
                record_mentions(session_id, now_served, repo_root=_srv._root)

            # Recompute derived fields after filtering
            ctx.files_referenced = sorted(set(n.file_path for n in ctx.nodes))
            ctx.token_estimate = sum(len(n.name) + len(n.docstring) + len(n.file_path) + 50 for n in ctx.nodes) // 4

        md = render_context_markdown(ctx)
        return json.dumps(
            {
                "context_markdown": md,
                "files_referenced": ctx.files_referenced,
                "symbol_count": len(ctx.nodes),
                "memory_docs_count": len(ctx.memory_docs),
                "plan_docs_count": len(ctx.plan_docs),
                "knowledge_docs_count": len(ctx.knowledge_docs),
                "ruflo_memory_hits": len(ctx.memory_hits),
                "estimated_tokens": ctx.token_estimate,
                "session_id": session_id or None,
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def session_reset(session_id: str) -> str:
        """
        Clear the dedup cache for a session — subsequent `context_for_task`
        calls with this session_id will be allowed to re-surface previously
        shown entities. Useful at the start of a new task within the same
        client session.
        """
        from codegraph.state.call_log import clear_session

        removed = clear_session(session_id, repo_root=_srv._root)
        return json.dumps({"session_id": session_id, "cleared": removed})

    @mcp.tool()
    @_logged_tool
    def call_stats() -> str:
        """
        Show codegraph usage statistics: total calls, per-tool breakdown,
        latency percentiles, error rate, and recent calls.
        """
        from codegraph.state.call_log import get_stats

        stats = get_stats(_srv._root)
        return json.dumps(stats, indent=2)
