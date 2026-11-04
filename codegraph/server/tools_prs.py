# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tool pr_overlap: the open pull requests of the repo that
#              change the same symbols, call into each other's changes, or
#              edit the same files (analysis/pr_overlap.py). Serves
#              `cgh prs overlap` while an owner holds the graph.

from __future__ import annotations

from codegraph.server._budget import dumps_answer


def register(mcp) -> None:
    """Register the pull-request tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.server import _get_conn, _logged_tool

    @mcp.tool()
    @_logged_tool
    def pr_overlap(limit: int = 30) -> str:
        """
        Which open pull requests of this repo collide. Reads the open PRs
        and their diffs with the GitHub CLI (`gh`, read-only), maps each
        diff onto the graph symbols it changes (like `impact_report`), and
        returns the PR pairs that overlap, strongest first:
          strong    both PRs change the same function or class
          indirect  code one PR changes calls a symbol the other changes
                    (one CALLS hop)
          weak      both PRs edit the same files, no shared symbol

        Args:
          limit: open PRs to read, newest first (default 30, max 100).

        Returns JSON `{prs, pairs: [{a, b, level, shared_symbols,
        shared_files, indirect}], counts, note}`, or `{error}` when gh is
        missing, not authenticated, or the repo has no GitHub remote.
        Parent scope only. Line numbers are matched against the local
        index, so it should be fresh on the PRs' base branch.
        """
        if _srv._root is None:
            return dumps_answer({"error": "owner root not initialised"})
        from codegraph.analysis.pr_overlap import pr_overlap as _overlap

        report = _overlap(_get_conn(), str(_srv._root), limit)
        return dumps_answer(report)
