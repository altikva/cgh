# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tool over the finding store: query what scanners know
#              about files (pii.*, secret.*, confidential, summary...).
#              Federated read-only across subrepos with a scope tag.

from __future__ import annotations

import json
import os


def register(mcp) -> None:
    """Register finding tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def findings(
        file_path: str = "", key_prefix: str = "", severity: str = "", limit: int = 100
    ) -> str:
        """
        Query scanner findings: what cgh knows about files beyond their
        code structure. Keys are namespaced by the scanner that wrote
        them (pii.email, secret.aws_key, confidential, summary, ...).

        Args:
          file_path:  restrict to one file (absolute, or relative to the
                      repo root). Empty = all files.
          key_prefix: e.g. "pii." or "secret". Empty = every key.
          severity:   "info" | "warn" | "block". Empty = all severities.
          limit:      per scope (default 100).

        Federated: children's finding stores are read read-only and every
        row carries a `scope` field (parent / <subrepo-name>).
        """
        from codegraph.analysis.federation import resolve_children
        from codegraph.state.findings import (
            findings_db_path,
            query_findings,
            query_findings_ro,
        )

        if file_path and not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)

        rows: list[dict] = []
        for row in query_findings(
            _srv._root,
            key_prefix=key_prefix,
            severity=severity,
            file_path=file_path,
            limit=limit,
        ):
            row["scope"] = "parent"
            rows.append(row)

        for child in resolve_children(_srv._root) if _srv._root else []:
            for row in query_findings_ro(
                findings_db_path(child), key_prefix=key_prefix, limit=limit
            ):
                if severity and row.get("severity") != severity:
                    continue
                if file_path and row.get("file") != file_path:
                    continue
                row["scope"] = child.name
                rows.append(row)

        return json.dumps({"total": len(rows), "findings": rows}, indent=2)
