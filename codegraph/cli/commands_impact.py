# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh impact --since <ref>` CI command for PR bots. Diffs the
#              working tree against a git ref, then reads the graph read-only
#              to report changed symbols, the IMPORTS blast radius grouped by
#              role / layer, endpoints touched, and tests to run. Emits JSON
#              (machine-parseable on stdout) or a markdown PR-comment summary.
#              Runs without an MCP owner: opens the graph DB read-only and
#              degrades gracefully when the index is missing or stale.

from __future__ import annotations

from codegraph.core.utils import quiet_subprocess_kwargs

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from codegraph.cli import LOGO

# Banner + notes go to stderr so stdout stays a clean JSON / markdown stream
# that a PR bot can pipe and parse.
_err = Console(stderr=True)


def _git_changed_files(root: str, since: str) -> tuple[list[str], str | None]:
    """Return (changed_files, error). Diffs the working tree against ``since``.

    Mirrors the validation in tools_index.index_changed_files: a leading dash
    is rejected so a value like "--output=/x" cannot be read as a git flag,
    and the trailing "--" keeps the ref from being parsed as a pathspec.
    """
    if since.startswith("-"):
        return [], f"invalid git ref: {since!r}"
    cmd = [
        "git",
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{since}...",
        "--",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
            timeout=30,
            **quiet_subprocess_kwargs(),
        )
    except Exception as exc:
        return [], f"git diff failed: {exc}"
    if result.returncode != 0:
        msg = (result.stderr or "").strip() or f"git diff exited {result.returncode}"
        return [], f"git diff failed: {msg}"
    files = [
        f.strip()
        for f in result.stdout.strip().splitlines()
        if f.strip() and not f.strip().startswith(".codegraph/")
    ]
    return files, None


def _build_report(conn, root: str, changed_files: list[str]) -> dict:
    """Assemble the impact report from the graph for the changed files.

    Uses the shared analysis helpers so the CLI and the MCP tools stay in
    lockstep. All paths returned to the caller are repo-relative.
    """
    from codegraph.analysis import impact as _impact

    root_path = Path(root).resolve()

    def _rel(p: str) -> str:
        try:
            return str(Path(p).resolve().relative_to(root_path))
        except (ValueError, OSError):
            return p

    # Changed files resolve to absolute File-node keys for graph lookups.
    abs_changed = [str((root_path / f)) for f in changed_files]

    changed_symbols: list[dict] = []
    for abs_f, rel_f in zip(abs_changed, changed_files):
        for sym in _impact.symbols_in_file(conn, abs_f):
            changed_symbols.append({"file": rel_f, **sym})

    # Blast radius: files that transitively import any changed file.
    radius, radius_trunc = _impact.reverse_import_bfs(conn, abs_changed, max_depth=3)

    impacted: list[dict] = []
    by_role: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for abs_p in radius:
        role, layer = _impact.file_role(conn, abs_p)
        impacted.append({"file": _rel(abs_p), "role": role, "layer": layer})
        if role:
            by_role[role] = by_role.get(role, 0) + 1
        if layer:
            by_layer[layer] = by_layer.get(layer, 0) + 1

    # Endpoints declared in the changed files OR any impacted file.
    endpoint_scope = abs_changed + radius
    endpoints = [
        {"file": _rel(e["file"]), "method": e["method"], "path": e["path"]}
        for e in _impact.endpoints_in_files(conn, endpoint_scope)
    ]

    # Tests to run: for each changed file, the test files that exercise it.
    test_seen: set[str] = set()
    tests: list[dict] = []
    for abs_f in abs_changed:
        for t in _impact.tests_for_file(conn, abs_f):
            rel_t = _rel(t["file"])
            if rel_t in test_seen:
                continue
            test_seen.add(rel_t)
            tests.append({"file": rel_t, "role": t["role"]})

    return {
        "since_changed": changed_files,
        "changed_symbols": changed_symbols,
        "impacted": impacted,
        "impacted_count": len(impacted),
        "impacted_by_role": by_role,
        "impacted_by_layer": by_layer,
        "endpoints": endpoints,
        "tests_to_run": tests,
        "truncated": radius_trunc,
        "note": (
            "Blast radius and tests are inferred from IMPORTS / CALLS edges, "
            "not a coverage run. Keep the index fresh with `cgh index` in CI."
        ),
    }


def _render_markdown(report: dict, since: str) -> str:
    """Render the report as a PR-comment-friendly markdown summary."""
    lines: list[str] = []
    lines.append(f"## cgh impact (since `{since}`)")
    lines.append("")

    changed = report["since_changed"]
    lines.append(f"**Changed files ({len(changed)})**")
    if changed:
        for f in changed:
            lines.append(f"- `{f}`")
    else:
        lines.append("- _none_")
    lines.append("")

    impacted = report["impacted"]
    lines.append(f"**Impacted files ({report['impacted_count']})**")
    if impacted:
        # Group by layer for a compact read.
        by_layer: dict[str, list[dict]] = {}
        for row in impacted:
            by_layer.setdefault(row.get("layer") or "other", []).append(row)
        for layer in sorted(by_layer):
            rows = by_layer[layer]
            lines.append(f"- _{layer}_ ({len(rows)})")
            for row in rows[:25]:
                role = row.get("role") or ""
                suffix = f" `{role}`" if role else ""
                lines.append(f"  - `{row['file']}`{suffix}")
            if len(rows) > 25:
                lines.append(f"  - _... {len(rows) - 25} more_")
    else:
        lines.append("- _none_")
    lines.append("")

    endpoints = report["endpoints"]
    lines.append(f"**Endpoints touched ({len(endpoints)})**")
    if endpoints:
        for e in endpoints:
            method = e.get("method") or "?"
            lines.append(f"- `{method} {e.get('path', '')}` ({e['file']})")
    else:
        lines.append("- _none_")
    lines.append("")

    tests = report["tests_to_run"]
    lines.append(f"**Tests to run ({len(tests)})**")
    if tests:
        for t in tests:
            lines.append(f"- `{t['file']}`")
    else:
        lines.append("- _no importing tests found_")
    lines.append("")

    if report.get("truncated"):
        lines.append("> Note: blast radius was truncated (large graph).")
    lines.append("")
    lines.append(f"> {report['note']}")
    return "\n".join(lines)


def cmd_impact(args: argparse.Namespace) -> None:
    """Handler for `cgh impact`. Non-MCP, CI-oriented: diffs against a ref,
    reads the graph read-only, and emits JSON or markdown."""
    root = os.path.abspath(args.root)
    since = getattr(args, "since", "HEAD~1") or "HEAD~1"

    # --json is shorthand for --format json; default format is markdown.
    fmt = getattr(args, "format", "md") or "md"
    if getattr(args, "json", False):
        fmt = "json"
    want_json = fmt == "json"

    # Banner to stderr only, never pollute the JSON / markdown on stdout.
    _err.print(LOGO)
    _err.print(
        "[dim]impact: diffing against "
        f"[/dim][cyan]{since}[/cyan][dim], reading graph read-only. "
        "Keep the index fresh with [/dim][cyan]cgh index[/cyan][dim] in CI.[/dim]\n"
    )

    if not (Path(root) / ".codegraph").is_dir():
        _fail(
            want_json,
            "repo is not indexed by cgh (.codegraph/ missing). "
            "Run `cgh init` then `cgh index`.",
        )
        return

    changed, err = _git_changed_files(root, since)
    if err is not None:
        _fail(want_json, err)
        return

    # Open the graph read-only directly, no MCP owner required. When an owner
    # holds the write lock, get_readonly_connection returns None; tell the
    # caller clearly rather than emitting a misleading empty report.
    from codegraph.core.db import get_readonly_connection

    conn = None
    try:
        conn = get_readonly_connection(root)
    except Exception as exc:
        _fail(want_json, f"could not open graph read-only: {exc}")
        return

    if conn is None:
        _fail(
            want_json,
            "graph DB is locked (an MCP owner is running) or missing. "
            "Stop the owner with `cgh serve --stop`, or run this in CI where "
            "no owner is alive.",
        )
        return

    report = _build_report(conn, root, changed)
    report["since"] = since

    if want_json:
        # Clean machine-parseable stdout.
        print(json.dumps(report, indent=2))
    else:
        print(_render_markdown(report, since))


def _fail(want_json: bool, message: str) -> None:
    """Emit a graceful error. JSON mode keeps stdout parseable with an
    {"error": ...} object; markdown mode writes the note to stderr."""
    if want_json:
        print(json.dumps({"error": message}, indent=2))
    else:
        _err.print(f"[yellow]{message}[/yellow]")
    sys.exit(1)
