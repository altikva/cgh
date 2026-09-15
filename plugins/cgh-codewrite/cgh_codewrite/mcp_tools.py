# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP tools codewrite_pick(target, reference?) and
#              code_write(spec, target, ...): pick the reference to mirror, and
#              generate the file from a spec behind the egress gate. Both run
#              inside the owner, so the graph read reuses the owner's
#              connection.

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

        @mcp.tool()
        def code_write(
            spec: str, target: str, reference: str = "", force: bool = False
        ) -> str:
            """
            Generate `target` from `spec` with a cheap model, mirroring an
            existing file's conventions (picked from the graph, or the one
            you pass as `reference`), and write it. Use this to hand off
            predictable, pattern-following code (stubs, config, boilerplate)
            so you spend no tokens producing it yourself. The reference is
            run through the egress gate before it reaches a cloud model; a
            confidential or PII-labeled reference is refused. An existing
            target is not overwritten unless `force` is true.

            The returned code is unverified: confirm it by running the
            type-checker, linter, or tests, never by trusting that it is
            correct because a later check was green.
            """
            from .backends import resolve_backend
            from .flow import run_generation
            from .generate import GenerationError
            from .picker import CodeWriteError

            root = server_root()
            if root is None:
                return json.dumps({"error": "no repo root"})
            backend = resolve_backend(config)
            if backend is None:
                return json.dumps(
                    {"error": "no backend configured ([plugin.codewrite] command)"}
                )
            try:
                result = run_generation(
                    root,
                    spec,
                    target,
                    reference or None,
                    config=config,
                    backend=backend,
                    force=force,
                )
            except (CodeWriteError, GenerationError) as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps(result, indent=2)

    return register_tools
