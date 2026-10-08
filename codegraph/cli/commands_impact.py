# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh impact --since <ref>` CI command for PR bots. Diffs the
#              working tree against a git ref, then reads the graph read-only
#              to report changed symbols, the IMPORTS blast radius grouped by
#              role / layer, endpoints touched, and tests to run. Emits JSON
#              (machine-parseable on stdout) or a markdown PR-comment summary.
#              Asks the repo's live owner when one holds the graph (the
#              impact_report MCP tool), else opens the graph DB read-only,
#              so it works both in CI and during an agent session.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from codegraph.cli import LOGO
from codegraph.core.utils import quiet_subprocess_kwargs

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
    """Assemble the impact report from a local read-only connection.

    Thin alias over the shared analysis helper so the CLI and the
    impact_report MCP tool stay in lockstep.
    """
    from codegraph.analysis.impact import build_impact_report

    return build_impact_report(conn, root, changed_files)


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
    """Handler for `cgh impact`. CI-oriented: diffs against a ref, reads the
    graph (through a live owner, else read-only), and emits JSON or markdown."""
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
        f"[/dim][cyan]{since}[/cyan][dim], reading the graph. "
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

    # A live owner holds the graph DB for writing, which blocks our own
    # read-only open, so ask it first. No owner (CI) -> open read-only here.
    # Never start an owner from this command.
    from codegraph.cli.owner_client import (
        call_owner_tool,
        note_route,
        older_owner_hint,
        stuck_owner_hint,
    )

    reply = call_owner_tool(root, "impact_report", {"changed_files": changed})
    if reply.ok and isinstance(reply.data, dict) and "error" not in reply.data:
        report = reply.data
        note_route("impact", "owner")
    elif reply.status == "timeout":
        # The owner is alive and holds the lock: a local open cannot succeed.
        _fail(want_json, stuck_owner_hint(root, reply))
        return
    elif reply.status == "unknown_tool":
        # An owner from an older cgh: alive, holding the lock, no such tool.
        _fail(want_json, older_owner_hint(root, "impact_report"))
        return
    else:
        report = _report_via_local_open(root, changed, reply, want_json)
        if report is None:
            return
        note_route("impact", "local read-only open")
    report["since"] = since

    from codegraph.cli.output import emit_result

    out = getattr(args, "out", "")
    if want_json:
        # Clean machine-parseable stdout.
        emit_result(json.dumps(report, indent=2), out=out, hint="impact.json")
    else:
        emit_result(_render_markdown(report, since), out=out, hint="impact.md")


def _report_via_local_open(
    root: str, changed: list[str], reply, want_json: bool
) -> dict | None:
    """Build the report from a local read-only open. ``reply`` is the owner
    attempt that preceded it; when an owner answered with an error, that
    error is folded into the failure message. Returns None after _fail."""
    from codegraph.cli.owner_client import stuck_owner_hint
    from codegraph.core.db import get_readonly_connection

    try:
        conn = get_readonly_connection(root)
    except Exception as exc:
        _fail(want_json, f"could not open graph read-only: {exc}")
        return None
    if conn is None:
        if reply.status == "error":
            _fail(want_json, stuck_owner_hint(root, reply))
        else:
            _fail(
                want_json,
                "graph DB is missing or locked by another cgh process. "
                "Run `cgh index` if the repo was never indexed.",
            )
        return None
    return _build_report(conn, root, changed)


def _fail(want_json: bool, message: str) -> None:
    """Emit a graceful error. JSON mode keeps stdout parseable with an
    {"error": ...} object; markdown mode writes the note to stderr."""
    if want_json:
        print(json.dumps({"error": message}, indent=2))
    else:
        _err.print(f"[yellow]{message}[/yellow]")
    sys.exit(1)
