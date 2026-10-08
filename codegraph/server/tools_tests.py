# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Test-to-code mapping MCP tools, computed on the fly from the
#              existing IMPORTS / CALLS edges plus File.role. No TESTS edge
#              type and no schema change. tests_for(symbol_or_file) surfaces
#              the test files that exercise a target; untested(role, layer)
#              lists source files no test imports. Both federate across
#              parent + subrepos and return JSON strings. impact_report
#              (parent only) serves `cgh impact` while an owner runs.

from __future__ import annotations

import json
import os

from codegraph.analysis import impact as _impact

# Cap untested output so a large repo cannot produce an unbounded list.
_UNTESTED_CAP = 200

# Shared caveat: this mapping is inferred from import / call edges, not from
# running a coverage tool. Keep the wording in one place.
_INFER_NOTE = (
    "Inferred from IMPORTS / CALLS edges plus File.role, not from a coverage "
    "run. A test counts if it imports the target file (or, for a symbol, calls "
    "it). Treat as a heuristic, not ground truth."
)


def register(mcp) -> None:
    """Register the test-mapping tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import federate_flat
    from codegraph.server import _get_conn, _logged_tool

    def _federate(query_fn):
        """Parent + federated children fan-out, flattened."""
        return federate_flat(_get_conn, _srv._root, query_fn)

    def _abs(path: str) -> str:
        """Resolve a repo-relative path against the parent root."""
        if not os.path.isabs(path) and _srv._root:
            return str(_srv._root / path)
        return path

    @mcp.tool()
    @_logged_tool
    def tests_for(symbol_or_file: str) -> str:
        """
        Which tests cover this symbol or file?
        Returns the test files that import the defining file and, for a
        symbol, the tests whose functions reach it through CALLS. Use it to
        pick the tests to run after an edit. Several changed files at once:
        impact_report.

        Args:
          symbol_or_file: a function / class name, or a repo-relative /
                          absolute file path.

        Returns JSON `{target, tests: [{file, role, scope}], count, note}`.
        This is an inferred import/call heuristic, NOT a coverage tool: see
        `note`. Federated across parent + subrepos; each test row carries a
        `scope` tag.
        """
        # Path-like args resolve against the parent root for the File lookup.
        looks_path = (
            "/" in symbol_or_file
            or "\\" in symbol_or_file
            or (os.path.splitext(symbol_or_file)[1] != "")
        )
        arg = _abs(symbol_or_file) if looks_path else symbol_or_file

        def query(conn):
            res = _impact.tests_for(conn, arg)
            return list(res["tests"])

        results, warnings = _federate(query)

        seen: set[tuple[str, str]] = set()
        tests: list[dict] = []
        for row in results:
            scope = row.get("scope", "parent")
            key = (scope, row.get("file", ""))
            if key in seen:
                continue
            seen.add(key)
            tests.append(
                {
                    "file": row.get("file", ""),
                    "role": row.get("role", ""),
                    "scope": scope,
                }
            )

        payload: dict = {
            "target": symbol_or_file,
            "tests": tests,
            "count": len(tests),
            "note": _INFER_NOTE,
        }
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)

    @mcp.tool()
    @_logged_tool
    def impact_report(changed_files: list[str]) -> str:
        """
        What does this change set break, and which tests should I run?
        For `changed_files`: the symbols they define, the files that
        transitively import them (by role and layer), endpoints touched and
        tests to run. Same payload as `cgh impact --json`. One symbol or
        file: impact_of or tests_for.

        Args:
          changed_files: repo-relative paths, e.g. from `git diff --name-only`.

        Parent scope only, like the CLI. Inferred from IMPORTS / CALLS
        edges, not coverage (see `note`).
        """
        if _srv._root is None:
            return json.dumps({"error": "owner root not initialised"})
        report = _impact.build_impact_report(
            _get_conn(), str(_srv._root), list(changed_files)
        )
        return json.dumps(report, indent=2)

    @mcp.tool()
    @_logged_tool
    def untested(role: str = "", layer: str = "") -> str:
        """
        Which source files have no test?
        Lists non-test source files that no test file imports; filter by
        role ("service", "router") or layer ("domain"). Test and doc files
        are never reported. Tests for one file: tests_for.

        Args:
          role:  optional File.role filter (exact match).
          layer: optional File.layer filter (exact match).

        Returns JSON `{untested: [{file, role, layer, scope}], count, note}`,
        capped at 200 with a truncation note. Inferred from import edges, not
        coverage (see `note`). Federated across parent + subrepos.
        """

        def query(conn):
            rows, _trunc = _impact.untested_files(
                conn, role=role, layer=layer, cap=_UNTESTED_CAP
            )
            if _trunc and rows:
                rows[-1] = {**rows[-1], "_trunc": True}
            return rows

        results, warnings = _federate(query)

        truncated = False
        untested_rows: list[dict] = []
        for row in results:
            if row.pop("_trunc", False):
                truncated = True
            scope = row.get("scope", "parent")
            untested_rows.append(
                {
                    "file": row.get("file", ""),
                    "role": row.get("role", ""),
                    "layer": row.get("layer", ""),
                    "scope": scope,
                }
            )

        if len(untested_rows) > _UNTESTED_CAP:
            untested_rows = untested_rows[:_UNTESTED_CAP]
            truncated = True

        payload: dict = {
            "untested": untested_rows,
            "count": len(untested_rows),
            "note": _INFER_NOTE,
        }
        if truncated:
            payload["truncated"] = True
            payload["truncation_note"] = f"capped at {_UNTESTED_CAP} files per scope"
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return json.dumps(payload, indent=2)
