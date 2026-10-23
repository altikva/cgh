# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex/substring pattern search across the indexed repo.
#              Replaces the need for Claude to call Grep / Read whole
#              files: returns structured hits with file + line + text.
#              group_hits groups a hit list by file and trims it to a
#              response size an agent can afford, each line clipped around
#              its match, and says where the hits it left out are.

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from codegraph.core.utils import quiet_subprocess_kwargs

# Match codegraph's IGNORE_DIRS to stay consistent with indexer behavior.
_IGNORE_DIR_NAMES = {
    ".git",
    ".codegraph",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".terraform",
    "dist",
    "build",
    ".next",
    ".turbo",
}

_MAX_LINE_LEN = 400  # truncate very long lines so JSON stays small
# What an MCP answer shows of each line, and the size of the hit list. Every
# byte of a tool answer is re-read on each later turn of the session: in a
# bench, three pattern_search answers of 16 to 30 KB took a fresh session
# from 61k to over 100k tokens within a minute.
SHOWN_LINE_LEN = 160
HITS_BUDGET_BYTES = 3_600
# Ceiling for the whole answer, hits, `more` and hint together.
PATTERN_ANSWER_BYTES = 4_096
# Files listed in `more` at most; the rest are only counted.
MORE_FILES_SHOWN = 10
# Hits a count_only search reads at least: it answers counts, not lines,
# so it can afford to look further than max_results.
COUNT_ONLY_MAX_RESULTS = 2_000


@dataclass
class PatternHit:
    file: str
    line: int
    text: str


def _expand_braces(glob: str) -> list[str]:
    """Shell-style brace expansion: ``*.{ts,vue}`` -> ``["*.ts", "*.vue"]``.

    ripgrep understands braces in a glob, but git pathspecs and fnmatch do
    not: handed ``*.{ts,vue}`` they match no file at all, and git grep reports
    that as a clean empty result, so the search silently returned nothing on
    any machine without ripgrep. Nested and repeated groups expand like the
    shell does. A brace with no top-level comma, or with no closing brace, is
    kept as a literal character. Order is preserved and duplicates dropped."""
    start = glob.find("{")
    while start != -1:
        depth = 0
        commas: list[int] = []
        for i in range(start, len(glob)):
            ch = glob[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    if commas:
                        bounds = [start, *commas, i]
                        head, tail = glob[:start], glob[i + 1 :]
                        out: list[str] = []
                        for a, b in pairwise(bounds):
                            for g in _expand_braces(head + glob[a + 1 : b] + tail):
                                if g not in out:
                                    out.append(g)
                        return out
                    break
            elif ch == "," and depth == 1:
                commas.append(i)
        start = glob.find("{", start + 1)
    return [glob]


def pattern_search(
    repo_root: str | Path,
    pattern: str,
    glob: str = "",
    max_results: int = 100,
    regex: bool = True,
    case_sensitive: bool = False,
    include_extra_dirs: bool = True,
) -> tuple[list[PatternHit], str]:
    """
    Structured pattern search across the indexed tree.
    Returns (hits, backend_used). Backend: rg | git-grep | python-fallback.

    Respects .gitignore via `git ls-files` when available, falls back to
    `_IGNORE_DIR_NAMES`. Scans `extra_dirs` from config.toml when
    include_extra_dirs=True.

    `glob` is a case-sensitive shell glob (e.g. "*.py", "api/handlers/*.py").
    `regex=False` treats the pattern as a plain substring (rg `--fixed-strings`).
    `max_results` is a hard cap to keep the response compact.
    """
    repo_root = Path(repo_root)
    if not pattern:
        return [], "empty"

    roots = [repo_root]
    if include_extra_dirs:
        try:
            import tomllib

            cfg = repo_root / ".codegraph" / "config.toml"
            if cfg.exists():
                with open(cfg, "rb") as f:
                    for rel in (
                        tomllib.load(f).get("codegraph", {}).get("extra_dirs", [])
                    ):
                        p = (repo_root / rel).resolve()
                        if p.exists() and p.is_dir():
                            roots.append(p)
        except Exception:
            pass

    # Strategy 1, ripgrep (preferred, fastest, respects .gitignore)
    if shutil.which("rg"):
        hits, ok = _run_rg(roots, pattern, glob, max_results, regex, case_sensitive)
        if ok:
            return hits[:max_results], "rg"

    # Strategy 2, git grep (respects .gitignore; repo-by-repo)
    if shutil.which("git"):
        hits, ok = _run_git_grep(
            roots, pattern, glob, max_results, regex, case_sensitive
        )
        if ok:
            return hits[:max_results], "git-grep"

    # Strategy 3, Python walk + re (slowest, always available)
    hits = _run_python(roots, pattern, glob, max_results, regex, case_sensitive)
    return hits[:max_results], "python-fallback"


def _run_rg(
    roots: list[Path],
    pattern: str,
    glob: str,
    max_results: int,
    regex: bool,
    case_sensitive: bool,
) -> tuple[list[PatternHit], bool]:
    out: list[PatternHit] = []
    for root in roots:
        args = [
            "rg",
            "--no-heading",
            "--line-number",
            "--max-count",
            str(max_results),
            "--json",
        ]
        if not case_sensitive:
            args.append("--ignore-case")
        if not regex:
            args.append("--fixed-strings")
        if glob:
            args.extend(["--glob", glob])
        # "--" stops the pattern from being parsed as a flag: without it a
        # pattern like "--pre=sh" reaches ripgrep's preprocessor (code exec).
        args.extend(["--", pattern, str(root)])
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                **quiet_subprocess_kwargs(),
            )
        except (subprocess.TimeoutExpired, OSError):
            return [], False
        if r.returncode not in (0, 1):  # 1 = no matches, still OK
            return [], False
        import json as _json

        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            try:
                obj = _json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data", {})
            path = data.get("path", {}).get("text") or ""
            line_no = data.get("line_number", 0)
            text = data.get("lines", {}).get("text") or ""
            if path and line_no:
                out.append(
                    PatternHit(
                        file=path,
                        line=line_no,
                        text=text.rstrip("\n")[:_MAX_LINE_LEN],
                    )
                )
                if len(out) >= max_results:
                    return out, True
    return out, True


def _run_git_grep(
    roots: list[Path],
    pattern: str,
    glob: str,
    max_results: int,
    regex: bool,
    case_sensitive: bool,
) -> tuple[list[PatternHit], bool]:
    out: list[PatternHit] = []
    for root in roots:
        # --untracked: a file just created and not yet added is searched too
        # (ripgrep does that already); .gitignore still applies.
        args = ["git", "grep", "-n", "-I", "--untracked"]
        if not case_sensitive:
            args.append("-i")
        if not regex:
            args.append("-F")
        else:
            args.append("-E")
        # "-e <pattern>" so a pattern starting with "-" is never read as a flag.
        args.extend(["-e", pattern])
        if glob:
            # One pathspec per brace alternative: git has no brace syntax.
            args.extend(["--", *_expand_braces(glob)])
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root),
                timeout=30,
                **quiet_subprocess_kwargs(),
            )
        except (subprocess.TimeoutExpired, OSError):
            return [], False
        if r.returncode not in (0, 1):
            return [], False
        for raw in r.stdout.splitlines():
            # Format: <path>:<line>:<text>
            parts = raw.split(":", 2)
            if len(parts) < 3:
                continue
            rel, ln, text = parts
            try:
                line_no = int(ln)
            except ValueError:
                continue
            out.append(
                PatternHit(
                    file=str((root / rel).resolve()),
                    line=line_no,
                    text=text[:_MAX_LINE_LEN],
                )
            )
            if len(out) >= max_results:
                return out, True
    return out, True


def _run_python(
    roots: list[Path],
    pattern: str,
    glob: str,
    max_results: int,
    regex: bool,
    case_sensitive: bool,
) -> list[PatternHit]:
    try:
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            rx = re.compile(pattern, flags)
        else:
            # Compile a fast literal matcher
            needle = pattern if case_sensitive else pattern.lower()
            rx = None  # sentinel, handled below
    except re.error:
        return []

    out: list[PatternHit] = []
    import fnmatch as _fn

    globs = _expand_braces(glob) if glob else []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _IGNORE_DIR_NAMES and not d.startswith(".")
            ]
            for filename in filenames:
                if globs and not any(_fn.fnmatch(filename, g) for g in globs):
                    continue
                full = Path(dirpath) / filename
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, start=1):
                            if rx is not None:
                                if rx.search(line):
                                    out.append(
                                        PatternHit(
                                            file=str(full),
                                            line=i,
                                            text=line.rstrip("\n")[:_MAX_LINE_LEN],
                                        )
                                    )
                            else:
                                haystack = line if case_sensitive else line.lower()
                                if needle in haystack:  # type: ignore[name-defined]
                                    out.append(
                                        PatternHit(
                                            file=str(full),
                                            line=i,
                                            text=line.rstrip("\n")[:_MAX_LINE_LEN],
                                        )
                                    )
                            if len(out) >= max_results:
                                return out
                except OSError:
                    continue
    return out


def match_finder(
    pattern: str, regex: bool = True, case_sensitive: bool = False
) -> Callable[[str], tuple[int, int] | None]:
    """A function giving the span of the first match of ``pattern`` in a
    line, or None. A regex Python cannot compile (ripgrep's syntax is a
    little wider) falls back to a literal search for the pattern."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)

    def find(text: str) -> tuple[int, int] | None:
        m = rx.search(text)
        return m.span() if m else None

    return find


def clip_line(
    text: str, span: tuple[int, int] | None, width: int = SHOWN_LINE_LEN
) -> str:
    """``text`` stripped and cut to about ``width`` characters around the
    match at ``span`` (the head of the line without one), with an ellipsis on
    each side that was cut."""
    if len(text) <= width:
        return text
    if span is None:
        return text[:width] + "…"
    s, e = span
    if e - s >= width:
        start = s
    else:
        start = s - (width - (e - s)) // 2
        start = max(0, min(start, len(text) - width))
    end = start + width
    return (
        ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
    )


def group_hits(
    hits: list[dict],
    find: Callable[[str], tuple[int, int] | None] | None = None,
    budget: int = HITS_BUDGET_BYTES,
    with_scope: bool = False,
) -> tuple[list[dict], int, dict[str, int]]:
    """Hits grouped by file, ``{"file", "hits": [[line, text], ...]}`` with
    the path written once, for as many hits as fit in ``budget`` bytes of
    JSON. Each line is stripped and clipped around its match (``find``).
    Returns the groups, the number of hits shown and, for the hits left out,
    how many per file. The first hit is always kept. ``with_scope`` tags each
    group with the hit's ``scope``, for a federated search."""
    groups: list[dict] = []
    index: dict[tuple[str, str], dict] = {}
    more: dict[str, int] = {}
    shown = 0
    used = 0
    for h in hits:
        file = h["file"]
        scope = h.get("scope", "parent")
        text = str(h.get("text", "")).strip()
        text = clip_line(text, find(text) if find else None)
        entry = [h["line"], text]
        cost = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 2
        group = index.get((scope, file))
        if group is None:
            cost += len(json.dumps(file, ensure_ascii=False).encode("utf-8")) + 24
            if with_scope:
                cost += len(scope) + 12
        if shown and (more or used + cost > budget):
            more[file] = more.get(file, 0) + 1
            continue
        if group is None:
            group = {"scope": scope} if with_scope else {}
            group.update({"file": file, "hits": []})
            index[(scope, file)] = group
            groups.append(group)
        group["hits"].append(entry)
        shown += 1
        used += cost
    return groups, shown, more
