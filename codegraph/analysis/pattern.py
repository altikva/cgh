# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex/substring pattern search across the indexed repo.
#              Replaces the need for Claude to call Grep / Read whole
#              files: returns structured hits with file + line + text.

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
        args = ["git", "grep", "-n", "-I"]
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
