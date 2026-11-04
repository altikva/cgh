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

import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
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
HITS_BUDGET_BYTES = 2_200
# Ceiling for the whole answer, hits, `more` and hint together. A token
# study found broad searches answering 4 KB, more than the Read they were
# meant to replace (3.5 KB on average): a cut answer names `top_dirs` and
# count_only instead of carrying more lines.
PATTERN_ANSWER_BYTES = 2_560
# Room a sentence's own fields (symbols, docs, interpreted_as, expanded)
# may take in that answer, so some grep lines still fit next to them.
SENTENCE_EXTRA_BYTES = 1_000
# Files listed in `more` at most; the rest are only counted.
MORE_FILES_SHOWN = 5
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


def _normalize_glob(glob: str) -> str:
    """A glob in ripgrep's sense, relative to the search root: a leading
    ``/`` anchors it at the root, and a glob with no ``/`` matches a file
    name at any depth, so ``module_*.py`` finds ``a/b/module_x.py`` too."""
    if glob.startswith("/"):
        return glob.lstrip("/")
    if "/" not in glob:
        return "**/" + glob
    return glob


def git_pathspecs(glob: str) -> list[str]:
    """``glob`` as git pathspecs with the same meaning as ripgrep's: one per
    brace alternative, with ``:(glob)`` magic so ``*`` stays inside one
    directory and ``**`` crosses them. A bare pathspec would anchor
    ``module_*.py`` at the root and miss every file in a subfolder."""
    return [":(glob)" + _normalize_glob(g) for g in _expand_braces(glob)]


def _glob_regex(glob: str) -> re.Pattern[str]:
    """A compiled regex fully matching the posix relative paths ``glob``
    selects, with the semantics of git_pathspecs."""
    g = _normalize_glob(glob)
    out: list[str] = []
    i = 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        elif g[i] == "[" and (end := g.find("]", i + 2)) != -1:
            body = g[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("".join(out), re.DOTALL)


@functools.cache
def git_has_pcre() -> bool:
    """Whether this git was built with PCRE, so ``git grep -P`` works.
    Asked once per process, in an empty directory outside any repo."""
    git = shutil.which("git")
    if not git:
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [git, "grep", "--no-index", "-q", "-P", "-e", r"\w"],
                capture_output=True,
                cwd=tmp,
                timeout=10,
                **quiet_subprocess_kwargs(),
            )
    except (subprocess.TimeoutExpired, OSError):
        return False
    # 1 = no match; a build without PCRE exits 128 with a fatal error.
    return r.returncode in (0, 1)


# Perl classes and their POSIX spelling, outside and inside a bracket.
_POSIX_CLASS = {
    "w": ("[[:alnum:]_]", "[:alnum:]_"),
    "W": ("[^[:alnum:]_]", None),
    "d": ("[0-9]", "0-9"),
    "D": ("[^0-9]", None),
    "s": ("[[:space:]]", "[:space:]"),
    "S": ("[^[:space:]]", None),
}
_ERE_ESCAPABLE = set(".[]()*+?{}|^$\\")


def to_posix_ere(pattern: str) -> str | None:
    """``pattern``, a Python-style regex, spelled for ``git grep -E``: the
    Perl classes become POSIX classes. None when ERE cannot say the same
    thing (word boundaries, lazy quantifiers, groups with ``(?``, other
    escapes), so the caller can pick a backend that can."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            if i + 1 >= n:
                return None
            nxt = pattern[i + 1]
            if nxt in _POSIX_CLASS:
                out.append(_POSIX_CLASS[nxt][0])
            elif nxt in _ERE_ESCAPABLE:
                out.append("\\" + nxt)
            elif nxt in "/-_:,=@#%&'\"<>~` ":
                # Escaped for Python's sake only; ERE leaves \x undefined.
                out.append(nxt)
            else:
                return None
            i += 2
        elif ch == "[":
            j = i + 1
            body = ["["]
            if j < n and pattern[j] == "^":
                body.append("^")
                j += 1
            if j < n and pattern[j] == "]":
                body.append("]")
                j += 1
            while j < n and pattern[j] != "]":
                c = pattern[j]
                if c == "\\":
                    inner = _POSIX_CLASS.get(pattern[j + 1 : j + 2], (None, None))[1]
                    if inner is None:
                        return None
                    body.append(inner)
                    j += 2
                elif c == "[" and pattern[j + 1 : j + 2] == ":":
                    end = pattern.find(":]", j + 2)
                    if end == -1:
                        return None
                    body.append(pattern[j : end + 2])
                    j = end + 2
                else:
                    body.append(c)
                    j += 1
            if j >= n:
                return None
            out.append("".join(body) + "]")
            i = j + 1
        elif ch in "(*+?}" and pattern[i + 1 : i + 2] == "?":
            # "(?" opens a group ERE has no syntax for; "*?" and kin are lazy.
            return None
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def git_regex_args(pattern: str) -> list[str] | None:
    """The ``git grep`` flags and ``-e`` pattern that give ``pattern`` the
    documented Python-style meaning: ``-P`` when git has PCRE, else ``-E``
    with the Perl classes spelled in POSIX. None when neither can say it."""
    if git_has_pcre():
        return ["-P", "-e", pattern]
    ere = to_posix_ere(pattern)
    return None if ere is None else ["-E", "-e", ere]


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

    `glob` is a case-sensitive shell glob (e.g. "*.py", "api/handlers/*.py");
    one without a "/" matches a file name at any depth, as in ripgrep.
    `pattern` is a Python-style regex (\\w, \\d, \\s, \\b, lazy quantifiers,
    classes) on every backend: git grep runs it with -P when git has PCRE,
    else with -E and POSIX classes, else the Python backend takes it.
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
        # ripgrep matches a glob holding a "/" against the path relative to
        # its working directory, so it runs inside the root: given the root
        # as an absolute path, "api/**" matched no file at all.
        args.extend(["--", pattern, "."])
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
                        file=str(root / path),
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
        # "-e <pattern>" so a pattern starting with "-" is never read as a flag.
        if not regex:
            args.extend(["-F", "-e", pattern])
        else:
            rx_args = git_regex_args(pattern)
            if rx_args is None:
                # ERE cannot express it and git has no PCRE: Python re can.
                return [], False
            args.extend(rx_args)
        if glob:
            args.extend(["--", *git_pathspecs(glob)])
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
    globs = [_glob_regex(g) for g in _expand_braces(glob)] if glob else []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _IGNORE_DIR_NAMES and not d.startswith(".")
            ]
            for filename in filenames:
                full = Path(dirpath) / filename
                if globs:
                    rel = full.relative_to(root).as_posix()
                    if not any(g.fullmatch(rel) for g in globs):
                        continue
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
