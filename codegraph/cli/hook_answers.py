# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Answer mode of the PreToolUse prechecks (CGH_HOOK_ANSWERS=1).
#              Instead of pointing the agent at a cgh tool, the hook runs the
#              lookup itself and hands over the result:
#              search_answer   a grep for a name: its definitions, read from
#                              the FTS, and its callers, asked of the live
#                              owner when there is one
#              outline_answer  a full read of an indexed source file: the
#                              file's symbols with their line ranges
#              Both return None when cgh has nothing to add, so the hook stays
#              silent rather than nudging on a search cgh cannot answer.

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

# Words in a search pattern that are syntax, not a name worth looking up.
_NOT_NAMES = frozenset(
    {
        "def", "class", "function", "const", "let", "var", "async", "await",
        "import", "from", "return", "self", "this", "export", "interface",
        "type", "public", "private", "protected", "static", "struct", "impl",
        "func", "TODO", "FIXME", "None", "True", "False", "null", "true",
        "false", "print",
    }
)  # fmt: skip
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
# \b, \w, \s... would otherwise glue a letter onto the next word.
_REGEX_ESCAPE_RE = re.compile(r"\\[A-Za-z]")
# Options of grep, git grep and rg that take their value as the next token.
_VALUED_OPTS = frozenset(
    {
        "-A", "-B", "-C", "-m", "-f", "-g", "-t", "-T", "--glob", "--type",
        "--max-count", "--context", "--after-context", "--before-context",
        "--include", "--exclude", "--exclude-dir", "-j", "--threads",
    }
)  # fmt: skip
_MAX_NAMES = 3
_MAX_DEFS = 4
_MAX_CALLERS = 5
_MAX_OUTLINE = 60
_MIN_OUTLINE_SYMBOLS = 5
# The hook runs before every search: an owner that does not answer fast is
# skipped, the definitions alone still go out.
_OWNER_TIMEOUT = 2.0


def enabled() -> bool:
    return os.environ.get("CGH_HOOK_ANSWERS") == "1"


def repo_root(start: Path) -> Path | None:
    """The nearest directory at or above ``start`` that holds a cgh FTS index."""
    for p in (start, *start.parents):
        if (p / ".codegraph" / "fts.db").is_file():
            return p
    return None


def search_parts(prog: str, args: list[str]) -> tuple[str | None, list[str]]:
    """The pattern of a grep, git grep or rg command line (args after the
    program, and after ``grep`` for git grep), and the paths it searches."""
    if prog == "git":
        args = args[1:]
    pattern: str | None = None
    paths: list[str] = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a in ("-e", "--regexp"):
            pattern = args[i + 1] if i + 1 < len(args) else None
            skip = True
            continue
        if a.startswith("--regexp="):
            pattern = a.split("=", 1)[1]
            continue
        if a in _VALUED_OPTS:
            skip = True
            continue
        if a.startswith("-") and a != "-":
            continue
        if pattern is None:
            pattern = a
        else:
            paths.append(a)
    return pattern, paths


def search_pattern(prog: str, args: list[str]) -> str | None:
    return search_parts(prog, args)[0]


def files_only(paths: list[str], cwd: Path) -> bool:
    """True when a search names its files one by one: the agent already knows
    where to look, and the definitions add nothing it does not have."""
    return bool(paths) and all((cwd / p).is_file() for p in paths)


def _looks_like_code(word: str) -> bool:
    return "_" in word or bool(_CAMEL_RE.search(word))


def names_in(pattern: str) -> list[str]:
    """Candidate symbol names in a search pattern, longest first.

    A one-word pattern is taken as a name. In a pattern of several words
    ("some random text") a plain word is prose, not a name: only words shaped
    like code (snake_case, camelCase) or following a keyword (``def foo``)
    count."""
    words = _WORD_RE.findall(_REGEX_ESCAPE_RE.sub(" ", pattern))
    prose = len(words) > 1
    seen: list[str] = []
    for i, w in enumerate(words):
        if w in _NOT_NAMES or w in seen:
            continue
        after_keyword = i > 0 and words[i - 1] in _NOT_NAMES
        if prose and not (after_keyword or _looks_like_code(w)):
            continue
        seen.append(w)
    return sorted(seen, key=len, reverse=True)[:_MAX_NAMES]


def _fts(root: Path) -> sqlite3.Connection | None:
    try:
        from codegraph.core.utils import ro_sqlite_uri

        return sqlite3.connect(
            ro_sqlite_uri(root / ".codegraph" / "fts.db"), uri=True, timeout=0.5
        )
    except sqlite3.Error:
        return None


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


def _definitions(conn: sqlite3.Connection, name: str) -> list[tuple]:
    # A method is stored qualified ("Class.method"): match the bare name too.
    return conn.execute(
        "SELECT kind, name, file_path, start_line, end_line FROM symbols "
        "WHERE (name = ? OR name LIKE ?) AND kind != 'md_section' "
        "ORDER BY name = ? DESC, file_path LIMIT ?",
        (name, f"%.{name}", name, _MAX_DEFS + 1),
    ).fetchall()


def _callers(root: Path, name: str) -> list[dict] | None:
    """Callers from the live owner, or None when no owner answers in time."""
    try:
        from codegraph.cli.owner_client import call_owner_tool

        reply = call_owner_tool(
            str(root), "find_callers", {"fn_name": name}, timeout=_OWNER_TIMEOUT
        )
    except Exception:
        return None
    if not reply.ok or not isinstance(reply.data, dict):
        return None
    callers = reply.data.get("callers")
    return callers if isinstance(callers, list) else None


def search_answer(root: Path, pattern: str) -> str | None:
    conn = _fts(root)
    if conn is None:
        return None
    blocks: list[str] = []
    try:
        names = names_in(pattern)
        for name in names:
            defs = _definitions(conn, name)
            # No definition, or so many that the name is too generic to point
            # anywhere: the text search is the better answer.
            if not defs or len(defs) > _MAX_DEFS:
                continue
            lines = [f"`{name}`:"]
            for kind, full, fp, sl, el in defs:
                lines.append(f"  defined  {full} ({kind})  {_rel(fp, root)}:{sl}-{el}")
            # Callers only for a search on one name: on an alternation they
            # multiply the answer and the agent rarely follows them.
            if len(names) > 1:
                blocks.append("\n".join(lines))
                continue
            callers = _callers(root, name)
            if callers:
                for c in callers[:_MAX_CALLERS]:
                    lines.append(
                        f"  called by {c.get('caller')}  "
                        f"{_rel(str(c.get('file', '')), root)}:{c.get('line')}"
                    )
                if len(callers) > _MAX_CALLERS:
                    lines.append(f"  ({len(callers) - _MAX_CALLERS} more callers)")
            elif callers is not None:
                lines.append("  no callers in the graph")
            blocks.append("\n".join(lines))
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not blocks:
        return None
    return (
        "[cgh] Already looked up from the code graph:\n"
        + "\n".join(blocks)
        + "\nRead those line ranges directly; the search output adds the text matches."
    )


def outline_answer(root: Path, target: Path) -> str | None:
    conn = _fts(root)
    if conn is None:
        return None
    try:
        abs_path = str(target.resolve())
        rel_path = _rel(abs_path, root)
        rows = conn.execute(
            "SELECT kind, name, start_line, end_line FROM symbols "
            "WHERE file_path IN (?, ?) AND kind != 'md_section' "
            "ORDER BY start_line LIMIT ?",
            (abs_path, rel_path, _MAX_OUTLINE + 1),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if len(rows) < _MIN_OUTLINE_SYMBOLS:
        return None
    lines = [
        f"  {sl}-{el}  {kind}  {name}" for kind, name, sl, el in rows[:_MAX_OUTLINE]
    ]
    if len(rows) > _MAX_OUTLINE:
        lines.append("  (more symbols: file_summary)")
    return (
        f"[cgh] Outline of {rel_path} from the code graph:\n"
        + "\n".join(lines)
        + "\nIf you need one part, Read it with offset/limit instead of the whole file."
    )
