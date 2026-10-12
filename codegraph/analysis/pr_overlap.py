# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Overlap between the open pull requests of a repo, for the
#              `cgh prs overlap` command and the pr_overlap MCP tool.
#              fetch_open_prs reads the open PRs and their diffs through the
#              GitHub CLI (read-only calls, nothing is sent), parse_diff_ranges
#              turns a unified diff into the base-side line ranges it touches,
#              and overlap_report maps those ranges onto graph symbols with
#              the same helper `cgh impact` uses, then pairs the PRs that
#              change the same symbols (strong), where one PR's changed code
#              calls a symbol the other changes (indirect, one hop), or that
#              only edit the same files (weak).

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from codegraph.analysis.impact import changed_code_symbols
from codegraph.core.utils import quiet_subprocess_kwargs

DEFAULT_LIMIT = 30
MAX_LIMIT = 100
_GH_TIMEOUT = 60
# Callees read per changed function, and rows kept per pair list.
_FANOUT_CAP = 500
_PAIR_ROWS_CAP = 50

STRONG = "strong"
INDIRECT = "indirect"
WEAK = "weak"
_LEVEL_RANK = {STRONG: 0, INDIRECT: 1, WEAK: 2}

OVERLAP_NOTE = (
    "Each PR's diff is mapped onto the local graph by its base-side line "
    "numbers, so the index should match the PRs' base branch. strong: both "
    "PRs change the same function or class. indirect: code one PR changes "
    "calls a symbol the other PR changes (one CALLS hop). weak: same files, "
    "no shared symbol. Files a PR adds are not in the graph and count at "
    "file level only."
)

_NO_GH = (
    "the GitHub CLI (gh) is not installed or not on PATH. Install it from "
    "https://cli.github.com and run `gh auth login`."
)
_NOT_AUTHED = "gh is not authenticated. Run `gh auth login`, then retry."
_NO_REMOTE = (
    "this repo has no GitHub remote gh can use (no git remote pointing at a "
    "GitHub host). Open PRs can only be read from a GitHub repository."
)


def _gh_env() -> dict[str, str]:
    """The environment for gh: never prompt, never print update notices."""
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _gh(root: str, *args: str) -> tuple[str, str | None]:
    """(stdout, error) of one gh command run in ``root``."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
            timeout=_GH_TIMEOUT,
            env=_gh_env(),
            **quiet_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return "", _NO_GH
    except subprocess.TimeoutExpired:
        return "", f"gh {args[0]} timed out after {_GH_TIMEOUT}s"
    except OSError as exc:
        return "", f"gh {args[0]} could not run: {exc}"
    if result.returncode != 0:
        return "", _classify_gh_error(result.stderr or "", args)
    return result.stdout, None


def _classify_gh_error(stderr: str, args: tuple[str, ...]) -> str:
    """A clear message for a failed gh call, from its stderr."""
    low = stderr.lower()
    if "gh auth login" in low or "not logged" in low or "authentication" in low:
        return _NOT_AUTHED
    if (
        "no git remotes" in low
        or "none of the git remotes" in low
        or "not a git repository" in low
        or "could not determine" in low
    ):
        return _NO_REMOTE
    first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
    return f"gh {' '.join(args[:2])} failed: {first or 'unknown error'}"


_DIFF_OLD = re.compile(r"^--- (?:a/)?(.+?)\s*$")
_DIFF_NEW = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _merge(lines: list[int]) -> list[tuple[int, int]]:
    """Sorted line numbers folded into (first, last) runs."""
    out: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if out and n <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], n))
        else:
            out.append((n, n))
    return out


def parse_diff_ranges(diff_text: str) -> dict[str, list[tuple[int, int]] | None]:
    """{path: base-side line ranges} of a unified diff, any context size.

    Ranges use the base (old) side of the diff: the local graph is indexed
    from the base branch, so those are the numbers its symbols carry. A
    removed or replaced line marks itself; a pure insertion marks the two
    base lines around it, so the block it lands in counts as changed. A
    file the diff adds (or one with no text hunk, a binary file) maps to
    None: changed as a whole, with no base lines to place."""
    out: dict[str, list[tuple[int, int]] | None] = {}
    marks: dict[str, list[int]] = {}
    old_path = ""
    path = ""
    old_line = 0
    # Lines left in the current hunk, per side: a hunk body line starting
    # with "--- " (a removed "-- comment") must not read as a file header.
    old_left = new_left = 0
    replacing = False
    for line in diff_text.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("\\"):
                continue
            if line.startswith("-"):
                marks[path].append(old_line)
                old_line += 1
                old_left -= 1
                replacing = True
            elif line.startswith("+"):
                # An added line right after removed ones replaces them: the
                # removed lines are already marked.
                if not replacing:
                    marks[path].extend((max(1, old_line - 1), max(1, old_line)))
                new_left -= 1
            else:
                old_line += 1
                old_left -= 1
                new_left -= 1
                replacing = False
            continue
        if line.startswith("diff --git "):
            old_path = path = ""
            continue
        m_old = _DIFF_OLD.match(line)
        if m_old and line.startswith("--- "):
            old_path = "" if m_old.group(1) == "/dev/null" else m_old.group(1)
            continue
        m_new = _DIFF_NEW.match(line)
        if m_new and line.startswith("+++ "):
            new_path = "" if m_new.group(1) == "/dev/null" else m_new.group(1)
            path = old_path or new_path
            if path:
                # A new file has no base side: the whole file changed.
                out[path] = None if not old_path else []
                marks.setdefault(path, [])
            continue
        m = _HUNK.match(line)
        if m and path:
            old_line = int(m.group(1))
            old_left = int(m.group(2) if m.group(2) is not None else 1)
            new_left = int(m.group(4) if m.group(4) is not None else 1)
            replacing = False
            if old_left == 0:
                # "-N,0": the insertion goes after base line N.
                old_line += 1
    for p, value in out.items():
        if value is None:
            continue
        ranges = _merge(marks.get(p, []))
        out[p] = ranges or None
    return out


def fetch_open_prs(
    root: str, limit: int = DEFAULT_LIMIT
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """(prs, warnings, error) for the open PRs of the repo at ``root``.

    Each PR is ``{number, title, head, url, draft, files: {path: ranges}}``
    with base-side ranges (see parse_diff_ranges). Only read-only gh calls
    are made. ``error`` is set, and nothing else is, when gh is missing,
    not authenticated or the repo has no GitHub remote. A PR whose diff
    gh cannot return (too large) falls back to its file list, at file
    level only, with a warning."""
    if shutil.which("gh") is None:
        return [], [], _NO_GH
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    out, err = _gh(
        root,
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,headRefName,url,isDraft",
    )
    if err is not None:
        return [], [], err
    try:
        listed = json.loads(out or "[]")
    except json.JSONDecodeError:
        return [], [], "gh pr list returned output that is not JSON"
    prs: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in listed if isinstance(listed, list) else []:
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        pr = {
            "number": number,
            "title": str(item.get("title") or ""),
            "head": str(item.get("headRefName") or ""),
            "url": str(item.get("url") or ""),
            "draft": bool(item.get("isDraft")),
            "files": {},
        }
        diff, derr = _gh(root, "pr", "diff", str(number), "--color", "never")
        if derr is None:
            pr["files"] = parse_diff_ranges(diff)
        else:
            files, ferr = _pr_file_list(root, number)
            pr["files"] = {f: None for f in files}
            pr["file_level_only"] = True
            warnings.append(
                f"PR {number}: diff unavailable ({derr}); "
                + ("compared at file level" if ferr is None else f"and {ferr}")
            )
        prs.append(pr)
    return prs, warnings, None


def _pr_file_list(root: str, number: int) -> tuple[list[str], str | None]:
    """The paths a PR changes, without line numbers (a fallback when its
    diff is too large for gh to return)."""
    out, err = _gh(root, "pr", "view", str(number), "--json", "files")
    if err is not None:
        return [], err
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return [], "gh pr view returned output that is not JSON"
    return [
        str(f.get("path"))
        for f in (data.get("files") or [])
        if isinstance(f, dict) and f.get("path")
    ], None


def _pr_symbols(
    conn: Any, root_path: Path, files: dict[str, list[tuple[int, int]] | None]
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """(changed symbols by id, {callee id: caller ids}) for one PR."""
    symbols: dict[str, dict[str, Any]] = {}
    fids: set[str] = set()
    for rel, ranges in files.items():
        abs_f = str(root_path / rel)
        syms, fn_ids, _ = changed_code_symbols(conn, abs_f, ranges)
        for s in syms:
            symbols[s["id"]] = {
                "name": s["name"],
                "kind": s["kind"],
                "file": rel,
                "lines": f"{s['start']}-{s['end']}",
            }
        fids.update(s["id"] for s in syms if s["kind"] == "function")
        fids.update(fn_ids)
    callees: dict[str, set[str]] = {}
    for fid in sorted(fids):
        for row in conn.find_neighbors(
            "CALLS", src_key=fid, return_dst=["id"], limit=_FANOUT_CAP
        ):
            dst = str(row.get("dst_id") or "")
            if dst and dst != fid:
                callees.setdefault(dst, set()).add(fid)
    return symbols, callees


def _sym_name(fn_id: str, known: dict[str, dict[str, Any]]) -> str:
    if fn_id in known:
        return str(known[fn_id]["name"])
    return fn_id.rsplit("::", 1)[-1]


def _indirect(
    a: dict[str, Any], b: dict[str, Any], root_path: Path
) -> list[dict[str, Any]]:
    """Rows where code PR ``a`` changes calls a symbol PR ``b`` changes."""
    rows: list[dict[str, Any]] = []
    for callee in sorted(set(a["callees"]) & set(b["symbols"])):
        if callee in a["symbols"]:
            continue  # both change it: already a strong overlap
        target = b["symbols"][callee]
        for caller in sorted(a["callees"][callee]):
            rows.append(
                {
                    "caller_pr": a["number"],
                    "caller": _sym_name(caller, a["symbols"]),
                    "caller_file": _rel(caller.rsplit("::", 1)[0], root_path),
                    "callee_pr": b["number"],
                    "callee": target["name"],
                    "callee_file": target["file"],
                }
            )
    return rows


def _rel(path: str, root_path: Path) -> str:
    try:
        return str(Path(path).relative_to(root_path))
    except ValueError:
        return path


def overlap_report(conn: Any, root: str, prs: list[dict[str, Any]]) -> dict:
    """Pairs of ``prs`` that overlap, strongest first.

    ``prs`` are the dicts fetch_open_prs returns (``files`` maps a
    repo-relative path to its base-side ranges, None for the whole file).
    Every pair with at least one shared file, shared symbol or one-hop call
    into the other PR's changes is reported with its ``level``."""
    root_path = Path(root).resolve()
    analysed: list[dict[str, Any]] = []
    for pr in prs:
        files = dict(pr.get("files") or {})
        symbols, callees = _pr_symbols(conn, root_path, files)
        analysed.append(
            {
                "number": pr["number"],
                "files": files,
                "symbols": symbols,
                "callees": callees,
            }
        )

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(analysed):
        for b in analysed[i + 1 :]:
            shared_files = sorted(set(a["files"]) & set(b["files"]))
            shared_ids = sorted(set(a["symbols"]) & set(b["symbols"]))
            indirect = _indirect(a, b, root_path) + _indirect(b, a, root_path)
            if shared_ids:
                level = STRONG
            elif indirect:
                level = INDIRECT
            elif shared_files:
                level = WEAK
            else:
                continue
            shared_symbols = [
                {k: a["symbols"][sid][k] for k in ("name", "kind", "file")}
                for sid in shared_ids
            ]
            pairs.append(
                {
                    "a": a["number"],
                    "b": b["number"],
                    "level": level,
                    "shared_symbols": shared_symbols[:_PAIR_ROWS_CAP],
                    "shared_files": shared_files[:_PAIR_ROWS_CAP],
                    "indirect": indirect[:_PAIR_ROWS_CAP],
                }
            )
    pairs.sort(
        key=lambda p: (
            _LEVEL_RANK[p["level"]],
            -len(p["shared_symbols"]),
            -len(p["indirect"]),
            -len(p["shared_files"]),
            p["a"],
            p["b"],
        )
    )
    summary = []
    for pr, info in zip(prs, analysed, strict=True):
        row = {
            "number": pr["number"],
            "title": pr.get("title", ""),
            "head": pr.get("head", ""),
            "url": pr.get("url", ""),
            "draft": bool(pr.get("draft")),
            "files": len(info["files"]),
            "changed_symbols": len(info["symbols"]),
        }
        if pr.get("file_level_only"):
            row["file_level_only"] = True
        summary.append(row)
    counts = {lvl: sum(1 for p in pairs if p["level"] == lvl) for lvl in _LEVEL_RANK}
    return {
        "prs": summary,
        "pairs": pairs,
        "counts": counts,
        "note": OVERLAP_NOTE,
    }


def pr_overlap(conn: Any, root: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Fetch the open PRs of ``root`` through gh and report their overlaps.
    Returns ``{"error": ...}`` instead of raising when gh cannot be used."""
    prs, warnings, err = fetch_open_prs(root, limit)
    if err is not None:
        return {"error": err}
    report = overlap_report(conn, root, prs)
    if warnings:
        report["warnings"] = warnings
    return report


def render_markdown(report: dict) -> str:
    """The overlap report as a short markdown summary."""
    lines = ["## cgh prs overlap", ""]
    prs = report.get("prs") or []
    titles = {p["number"]: p.get("title", "") for p in prs}
    lines.append(f"**Open PRs ({len(prs)})**")
    for p in prs:
        extra = " (file level only)" if p.get("file_level_only") else ""
        lines.append(
            f"- PR {p['number']} `{p.get('head', '')}`: {p.get('title', '')} "
            f"({p['files']} files, {p['changed_symbols']} symbols){extra}"
        )
    if not prs:
        lines.append("- _none_")
    lines.append("")
    pairs = report.get("pairs") or []
    lines.append(f"**Overlapping pairs ({len(pairs)})**")
    if not pairs:
        lines.append("- _none_")
    for p in pairs:
        lines.append(
            f"- **{p['level']}** PR {p['a']} and PR {p['b']} "
            f"({titles.get(p['a'], '')} / {titles.get(p['b'], '')})"
        )
        for s in p["shared_symbols"]:
            lines.append(f"  - both change `{s['name']}` ({s['kind']}, `{s['file']}`)")
        for r in p["indirect"]:
            lines.append(
                f"  - PR {r['caller_pr']} `{r['caller']}` calls `{r['callee']}` "
                f"changed by PR {r['callee_pr']} (`{r['callee_file']}`)"
            )
        if p["shared_files"]:
            files = ", ".join(f"`{f}`" for f in p["shared_files"])
            lines.append(f"  - shared files: {files}")
    lines.append("")
    for w in report.get("warnings") or []:
        lines.append(f"> Warning: {w}")
    lines.append(f"> {report.get('note', OVERLAP_NOTE)}")
    return "\n".join(lines)
