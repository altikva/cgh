# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools: summaries(file_path?) serving stored summaries
#              (federated read-only over subrepos) and corpus_insights()
#              running the cross-file pass and persisting it to the
#              knowledge store.

from __future__ import annotations

import json
import os


def make_mcp_registrar(config: dict, extras_fn):
    def register_tools(mcp) -> None:
        from codegraph.plugin_api import server_root

        @mcp.tool()
        def summaries(file_path: str = "", limit: int = 50) -> str:
            """
            Stored prose summaries of indexed files (written by the
            summarize scanner). One summary per file, cheap to read:
            prefer this over reading a large file when you only need
            its gist. Federated: children's summaries come back with a
            scope tag.
            """
            from codegraph.plugin_api import resolve_children
            from codegraph.plugin_api import (
                findings_db_path,
                query_findings,
                query_findings_ro,
            )

            if file_path and not os.path.isabs(file_path) and server_root():
                file_path = str(server_root() / file_path)

            rows: list[dict] = []
            for row in query_findings(
                server_root(), key_prefix="summary", file_path=file_path, limit=limit
            ):
                if row["key"] != "summary":
                    continue
                rows.append(
                    {"scope": "parent", "file": row["file"], "summary": row["value"]}
                )
            for child in resolve_children(server_root()) if server_root() else []:
                for row in query_findings_ro(
                    findings_db_path(child), key_prefix="summary", limit=limit
                ):
                    if row["key"] != "summary":
                        continue
                    if file_path and row["file"] != file_path:
                        continue
                    rows.append(
                        {
                            "scope": child.name,
                            "file": row["file"],
                            "summary": row["value"],
                        }
                    )
            return json.dumps({"total": len(rows), "summaries": rows}, indent=2)

        @mcp.tool()
        def corpus_insights(question: str = "") -> str:
            """
            Batch the gate-cleared file summaries into one model call
            and surface cross-file patterns: duplicated concepts,
            architectural drift, surprising couplings. The result is
            persisted to the knowledge store (tags: insights) so later
            sessions recall it instead of re-deriving it. Optionally
            pass a specific question to ask the corpus.
            """
            from .insights import run_insights

            result = run_insights(
                server_root(), config, extras_fn=extras_fn, question=question
            )
            return json.dumps(result, indent=2)

    return register_tools
