# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP meta tools: fts_search, dead_code, context_for_task, call_stats.

from __future__ import annotations

from codegraph.core.utils import short_path as _short_path
from codegraph.server._budget import dumps_answer


def register(mcp) -> None:
    """Register meta tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _get_fts, _logged_tool

    @mcp.tool()
    @_logged_tool
    def fts_search(query: str, limit: int = 15, kind: str = "") -> str:
        """
        Find CODE about a concept described in words: full-text search over
        symbol names and docstrings, BM25-ranked. A whole sentence works,
        in French or English ("relance des webhooks en echec", "how the
        owner resumes a session"): stopwords are dropped and any remaining
        term matches. Use it when you know what the code does but not what
        it is called; use symbol_lookup when you know the name and
        pattern_search for a literal string. Federated across subrepos. Each result tagged with `scope`. The
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
                "lines": f"{r.start_line}-{r.end_line}"
                if r.end_line
                else str(r.start_line),
                "doc": r.docstring,
                "score": round(r.score, 4),
            }

        all_results: list[dict] = []
        warnings: list[dict] = []

        # Parent, use cached conn from server
        try:
            parent_results = _fts(
                _get_fts(),
                query,
                limit=limit,
                kind_filter=kind if kind else None,
            )
            all_results.extend(_result_to_dict(r, "parent") for r in parent_results)
        except Exception as exc:
            warnings.append(
                {"scope": "parent", "error": f"{type(exc).__name__}: {exc}"}
            )

        # Children, fresh RO conns
        if _srv._root is not None:
            for scoped in for_each_child_fts(
                _srv._root,
                lambda c, _r: _fts(
                    c, query, limit=limit, kind_filter=kind if kind else None
                ),
            ):
                if scoped.error:
                    warnings.append({"scope": scoped.scope, "error": scoped.error})
                    continue
                all_results.extend(
                    _result_to_dict(r, scoped.scope) for r in scoped.payload or []
                )

        # Sort across federation by score (BM25 returns negative, higher abs is better)
        all_results.sort(key=lambda x: -x["score"])

        out: dict = {"query": query, "results": all_results}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

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
        the parent or another subrepo, we don't infer cross-repo edges.
        Treat the results as a per-scope candidate list, not a hard verdict.
        """
        from codegraph.analysis.dead_code import find_dead_code as _find_dead
        from codegraph.analysis.federation import for_each_child_graphdb

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
            warnings.append(
                {"scope": "parent", "error": f"{type(exc).__name__}: {exc}"}
            )

        if _srv._root is not None:
            for scoped in for_each_child_graphdb(
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
            "note": "per-scope analysis, a symbol may be live via cross-repo callers",
        }
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    @mcp.tool()
    @_logged_tool
    def context_for_task(
        task: str,
        max_nodes: int = 10,
        session_id: str = "",
        include_shown: bool = False,
    ) -> str:
        """
        THE FIRST TOOL TO CALL for any coding task.
        Given a natural-language task description, builds a compact, ranked context
        block containing the most relevant symbols, their docstrings, and their
        graph relationships, plus Claude Code memory entries and related plan
        files, WITHOUT reading any files.

        Use this before any file reads: one call instead of several searches
        and file reads.

        Args:
            task: description of what you need to do
                  e.g. "fix the authentication token validation logic"
                       "add a new GCS bucket resource"
                       "refactor the DataLoader class"
            max_nodes: max symbols to include (default 10)
            session_id: optional, when passed, already-surfaced entities
                  from previous calls in THIS session are hidden. Pass the
                  same id across calls to avoid re-serving the same nodes.
            include_shown: if true, ignore session dedup and return
                  everything. Useful to review what was previously served.

        Returns structured markdown context + hit counts. Paths are relative
        to the repo root. Each symbol comes with its line range and one to
        three lines saying why it was picked (the docstring sentence that
        matches, a call site, or the matching source line), often enough to
        act without reading the file. `start_here` lists the best entry
        files (at most 3), `related` the next five, and `next` the one call
        or ranged Read to make next. Matching memory, plans and knowledge
        entries come as one-line pointers (a title, plus the plan file or
        the knowledge id): memory_search, plan_search or knowledge_search
        brings their text. In a federated workspace the subrepos are
        searched too: their symbols show their scope, and their files read
        `<scope>:<path>`, the path relative to that subrepo's root. When the
        served functions and classes carry rationale comments (WHY:, NOTE:,
        HACK:, ... or an ADR reference), a short `rationale` list gives them
        ({file, line, tag, text, symbol, symbol_kind, adr?, adr_file?}).
        """
        from codegraph.analysis.context_builder import context_for_task as _ctx
        from codegraph.server._budget import CONTEXT_MAX_BYTES, TOOL_CAPS, dumps_answer
        from codegraph.state.call_log import filter_unseen, record_mentions

        ctx = _ctx(
            task=task,
            graph_conn=_get_conn(),
            fts_conn=_get_fts(),
            max_nodes=max_nodes,
            root=_srv._root,
        )
        warnings = _federate_nodes(ctx, task, max_nodes)

        # Session-scoped dedup
        if session_id and not include_shown:
            from codegraph.state.activity import log as _activity_log

            served_nodes = [
                ("symbol", f"{n.file_path}:{n.start_line}") for n in ctx.nodes
            ]
            served_mem = [("memory", m.path) for m in ctx.memory_docs]
            served_plans = [("plan", p.path) for p in ctx.plan_docs]
            served_know = [("knowledge", str(k.id)) for k in ctx.knowledge_docs]
            all_entities = served_nodes + served_mem + served_plans + served_know

            unseen = set(filter_unseen(session_id, all_entities, repo_root=_srv._root))
            before = len(ctx.nodes) + len(ctx.memory_docs) + len(ctx.plan_docs)

            ctx.nodes = [
                n
                for n in ctx.nodes
                if ("symbol", f"{n.file_path}:{n.start_line}") in unseen
            ]
            ctx.memory_docs = [
                m for m in ctx.memory_docs if ("memory", m.path) in unseen
            ]
            ctx.plan_docs = [p for p in ctx.plan_docs if ("plan", p.path) in unseen]
            ctx.knowledge_docs = [
                k for k in ctx.knowledge_docs if ("knowledge", str(k.id)) in unseen
            ]

            after = (
                len(ctx.nodes)
                + len(ctx.memory_docs)
                + len(ctx.plan_docs)
                + len(ctx.knowledge_docs)
            )
            if before != after:
                try:
                    _activity_log(
                        _srv._root,
                        "session_dedup",
                        f"{session_id} hid {before - after}",
                    )
                except Exception:
                    pass

            # Recompute derived fields after filtering
            ctx.files_referenced = sorted(set(n.file_path for n in ctx.nodes))
            ctx.token_estimate = _token_estimate(ctx)

        # Trimmed before the session record below: what the cut leaves out
        # was not served, so the next call with this session_id gets it.
        subsystem = _subsystem_of(ctx)
        dropped = _fit_context(ctx, subsystem, session_id, task)
        if dropped:
            ctx.token_estimate = _token_estimate(ctx)

        if session_id and not include_shown:
            # Record what we're about to serve
            now_served = (
                [("symbol", f"{n.file_path}:{n.start_line}") for n in ctx.nodes]
                + [("memory", m.path) for m in ctx.memory_docs]
                + [("plan", p.path) for p in ctx.plan_docs]
                + [("knowledge", str(k.id)) for k in ctx.knowledge_docs]
            )
            if now_served:
                record_mentions(session_id, now_served, repo_root=_srv._root)

        payload = _context_payload(ctx, session_id, subsystem, task)
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        if dropped:
            payload["truncated"] = True
            payload["dropped"] = dropped
            payload["hint"] = (
                f"Answer cut to about {CONTEXT_MAX_BYTES // 1000} KB. "
                + TOOL_CAPS["context_for_task"].hint
            )
        return dumps_answer(payload)

    def _federate_nodes(ctx, task: str, max_nodes: int) -> list[dict]:
        """Add the best symbols of each federated subrepo to ``ctx``, ranked
        with the parent's; returns a warning per subrepo that failed."""
        if _srv._root is None:
            return []
        from codegraph.analysis.context_builder import code_nodes, merge_nodes
        from codegraph.analysis.federation import for_each_child_graphdb, open_fts_ro

        def one(graph, child_root):
            with open_fts_ro(child_root) as fts:
                if fts is None:
                    raise RuntimeError("fts index unavailable")
                return code_nodes(
                    task,
                    graph,
                    fts,
                    max_nodes=max_nodes,
                    root=child_root,
                    scope=child_root.name,
                )

        warnings: list[dict] = []
        extra = []
        for scoped in for_each_child_graphdb(_srv._root, one):
            if scoped.error:
                warnings.append({"scope": scoped.scope, "error": scoped.error})
                continue
            extra.extend(scoped.payload or [])
        if extra:
            ctx.nodes = merge_nodes(ctx.nodes, extra, max_nodes)
            ctx.files_referenced = sorted(set(n.file_path for n in ctx.nodes))
            ctx.token_estimate = _token_estimate(ctx)
        return warnings

    def _token_estimate(ctx) -> int:
        from codegraph.analysis.context_builder import token_estimate

        return token_estimate(ctx.nodes)

    def _context_payload(
        ctx, session_id: str, subsystem: dict | None, task: str = ""
    ) -> dict:
        """The answer for ``ctx``, with the ``subsystem`` hint when one (as a
        key only, not repeated in the markdown)."""
        from codegraph.analysis.context_builder import (
            file_tiers,
            next_step,
            render_context_markdown,
        )

        md = render_context_markdown(ctx)
        start_here, related = file_tiers(ctx.nodes)
        payload = {
            "context_markdown": md,
            "start_here": start_here,
            "related": related,
            "next": next_step(ctx.nodes, start_here, task or ctx.task, _srv._root),
            "symbol_count": len(ctx.nodes),
            "memory_docs_count": len(ctx.memory_docs),
            "plan_docs_count": len(ctx.plan_docs),
            "knowledge_docs_count": len(ctx.knowledge_docs),
            "estimated_tokens": ctx.token_estimate,
            "session_id": session_id or None,
        }
        rationale = _rationale_of(ctx)
        if rationale:
            payload["rationale"] = rationale
        if subsystem:
            payload["subsystem"] = subsystem
        return payload

    # Trim order when the context overflows its ceiling: the lists are ranked
    # best first, so items go from the tail. A symbol weighs about four times
    # a one-line memory, plan or knowledge pointer, so the symbols go down to
    # five first, then the pointers to one each, the symbols to one, and the
    # last pointers.
    _CONTEXT_TRIM = (
        ("nodes", 5),
        ("plan_docs", 1),
        ("memory_docs", 1),
        ("knowledge_docs", 1),
        ("nodes", 1),
        ("plan_docs", 0),
        ("memory_docs", 0),
        ("knowledge_docs", 0),
    )

    def _fit_context(
        ctx, subsystem: dict | None, session_id: str = "", task: str = ""
    ) -> int:
        """Drop the lowest-ranked items of ``ctx`` until its answer, measured
        as received and with the same session_id and task, fits
        CONTEXT_MAX_BYTES; returns how many went."""
        from codegraph.server._budget import (
            CONTEXT_MAX_BYTES,
            TOOL_CAPS,
            answer_bytes,
        )

        # Room for the truncated, dropped and hint keys added when cut.
        budget = CONTEXT_MAX_BYTES - len(TOOL_CAPS["context_for_task"].hint) - 80
        dropped = 0
        while answer_bytes(_context_payload(ctx, session_id, subsystem, task)) > budget:
            for attr, floor in _CONTEXT_TRIM:
                items = getattr(ctx, attr)
                if len(items) > floor:
                    items.pop()
                    dropped += 1
                    ctx.files_referenced = sorted(set(n.file_path for n in ctx.nodes))
                    break
            else:
                break
        return dropped

    def _rationale_of(ctx) -> list[dict]:
        """The rationale comments of the served symbols, flat and short, the
        same items the context markdown lists under each symbol."""
        from codegraph.analysis.rationale import RATIONALE_TEXT_CAP, item

        out = []
        for n in ctx.nodes:
            root = n.scope_root or _srv._root
            for r in n.rationale:
                row = item(
                    r, RATIONALE_TEXT_CAP, lambda p, root=root: _short_path(p, root)
                )
                if n.scope != "parent":
                    row["scope"] = n.scope
                out.append(row)
        return out

    def _subsystem_of(ctx) -> dict | None:
        """The community the best-matching symbols live in, with its central
        files the context did not already list. Best effort: the context
        answers without it on any failure."""
        if not ctx.nodes:
            return None
        try:
            from codegraph.analysis.communities import communities_for, subsystem_hint

            result = communities_for(_get_conn())
            seeds = [n.file_path for n in ctx.nodes]
            hint = subsystem_hint(
                result, seeds, exclude=ctx.files_referenced, max_files=3
            )
        except Exception:  # best effort, see the docstring
            return None
        if hint is None:
            return None
        full = next(c for c in result.communities if c.id == hint.id)
        return {
            "name": hint.name,
            "size": full.size,
            "files": [_short_path(f, _srv._root) for f in hint.files],
        }

    @mcp.tool()
    @_logged_tool
    def session_reset(session_id: str) -> str:
        """
        Clear the dedup cache for a session, subsequent `context_for_task`
        calls with this session_id will be allowed to re-surface previously
        shown entities. Useful at the start of a new task within the same
        client session.
        """
        from codegraph.state.call_log import clear_session

        removed = clear_session(session_id, repo_root=_srv._root)
        return dumps_answer({"session_id": session_id, "cleared": removed})

    @mcp.tool()
    @_logged_tool
    def call_stats() -> str:
        """
        Show codegraph usage statistics: total calls, per-tool breakdown,
        latency percentiles, error rate, and recent calls.
        """
        from codegraph.state.call_log import get_stats

        stats = get_stats(_srv._root)
        return dumps_answer(stats)
