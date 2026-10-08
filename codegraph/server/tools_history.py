# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Git-history MCP tools. hotspots joins per-file churn (from
#              analysis.churn over the parent root) with graph centrality
#              (in-degree on the IMPORTS edge) to rank change-risk files.
#              who_knows rolls up the top authors of one file from the git
#              log. Both read _srv._root at call time and return JSON strings.

from __future__ import annotations

import json
import math
import os
import time

# Cap on the number of files we score / return.
_HOTSPOT_SCAN_CAP = 2000


def register(mcp) -> None:
    """Register git-history tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis import churn as _churn
    from codegraph.server import _get_conn, _logged_tool

    def _abs(path: str) -> str:
        """Resolve a repo-relative path against the parent root."""
        if not os.path.isabs(path) and _srv._root:
            return str(_srv._root / path)
        return path

    @mcp.tool()
    @_logged_tool
    def hotspots(limit: int = 20) -> str:
        """
        Where would a regression hurt most?
        Ranks files that change often in git AND are imported by many
        files: review and refactor targets, and code to touch carefully. Who
        knows a file: who_knows.

        Args:
          limit: how many top files to return (default 20).

        Returns JSON {hotspots: [{file, commits, last_modified, importers,
        authors, score}], count, scanned, note}. NOT federated: git churn is
        the parent repo's history only.
        """
        root = _srv._root
        if root is None:
            return json.dumps({"hotspots": [], "count": 0, "error": "no repo root"})

        churn = _churn.file_churn(root)
        if not churn:
            return json.dumps(
                {
                    "hotspots": [],
                    "count": 0,
                    "scanned": 0,
                    "note": "no git history available (not a git repo or git missing)",
                }
            )

        # In-degree over IMPORTS, counted once for the whole graph. Each
        # IMPORTS edge is (src File) -> (dst File); the dst gains one importer.
        importers: dict[str, int] = {}
        try:
            conn = _get_conn()
            for r in conn.find_neighbors(
                "IMPORTS", return_dst=["path"], limit=_HOTSPOT_SCAN_CAP * 20
            ):
                dst = r.get("dst_path")
                if dst:
                    importers[dst] = importers.get(dst, 0) + 1
        except Exception:
            importers = {}

        # Map churn (repo-relative paths) onto graph paths (absolute) so we
        # can attach the importer count.
        items = list(churn.items())[:_HOTSPOT_SCAN_CAP]
        max_commits = max((e["commits"] for _p, e in items), default=1)
        max_importers = max(importers.values(), default=1) if importers else 1
        now = time.time()
        log_commits = math.log1p(max_commits)
        log_importers = math.log1p(max_importers)

        scored: list[dict] = []
        for rel_path, e in items:
            abs_path = _abs(rel_path)
            imp = importers.get(abs_path, 0)
            commits = e["commits"]
            last = e.get("last_modified", 0) or 0

            commit_term = math.log1p(commits) / log_commits if log_commits else 0.0
            import_term = (
                math.log1p(imp) / log_importers if log_importers and imp else 0.0
            )
            if last > 0:
                age_days = max(0.0, (now - last) / 86400.0)
                recency_term = 1.0 / (1.0 + age_days / 30.0)
            else:
                recency_term = 0.0
            score = round(
                100.0 * (0.45 * commit_term + 0.35 * import_term + 0.20 * recency_term),
                2,
            )

            # Authors as a sorted [name, commits] list, top few only.
            authors = sorted(
                e.get("authors", {}).items(), key=lambda kv: kv[1], reverse=True
            )[:5]
            scored.append(
                {
                    "file": rel_path,
                    "commits": commits,
                    "last_modified": last,
                    "importers": imp,
                    "authors": [{"name": n, "commits": c} for n, c in authors],
                    "score": score,
                }
            )

        scored.sort(key=lambda r: r["score"], reverse=True)
        top = scored[: max(1, int(limit))]
        return json.dumps(
            {
                "hotspots": top,
                "count": len(top),
                "scanned": len(items),
                "note": (
                    f"churn covers the last {_churn.DEFAULT_COMMIT_CAP} commits; "
                    "score combines commit count (0.45), import in-degree "
                    "(0.35), and recency (0.20). Git history is the parent "
                    "repo only, not federated."
                ),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def who_knows(file_path: str) -> str:
        """
        Who knows this file?
        Top authors of `file_path` by commit count and recency, from git
        log. Use it to pick a reviewer or see who last changed code you are
        about to edit.

        Args:
          file_path: repo-relative or absolute path to the file.

        Returns JSON {file, authors: [{name, commits, last_commit}], note}.
        last_commit is a unix timestamp (seconds). NOT federated: ownership
        is computed from the parent repo's git history.
        """
        root = _srv._root
        if root is None:
            return json.dumps(
                {"file": file_path, "authors": [], "error": "no repo root"}
            )

        authors = _churn.file_ownership(root, file_path)
        note = (
            "authors ranked by commit count then recency, from the last "
            f"{_churn.OWNERSHIP_COMMIT_CAP} commits touching this file"
        )
        if not authors:
            note = (
                "no git history for this file (not tracked, not a git repo, "
                "or git missing)"
            )
        return json.dumps(
            {
                "file": file_path,
                "authors": authors,
                "note": note,
            },
            indent=2,
        )
