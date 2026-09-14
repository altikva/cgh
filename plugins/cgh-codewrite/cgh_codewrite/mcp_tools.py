# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tool codewrite_pick(target, reference?): the reference an
#              agent should mirror to generate a file, chosen from the graph
#              plus the target directory. Runs inside the owner, so its graph
#              read reuses the owner's connection.

from __future__ import annotations

import json


def make_mcp_registrar(config: dict):
    def register_tools(mcp) -> None:
        from codegraph.plugin_api import server_root

        @mcp.tool()
        def codewrite_pick(target: str, reference: str = "") -> str:
            """
            Pick the existing file to mirror when generating `target`
            (a path you intend to write, e.g. tests/test_user_service.py).
            Combines the code graph (a file defining a symbol related to
            the target's name) with the target's own directory (a sibling
            of the same kind), and returns the chosen reference, the reason,
            and the runner-up candidates. Use it before writing boilerplate
            so the new file matches an established pattern. Pass `reference`
            to validate a specific file instead of picking one.
            """
            from .picker import CodeWriteError, pick_reference

            root = server_root()
            if root is None:
                return json.dumps({"error": "no repo root"})
            try:
                result = pick_reference(root, target, reference or None)
            except CodeWriteError as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps(result, indent=2)

    return register_tools
