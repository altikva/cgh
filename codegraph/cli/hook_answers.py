# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Answer mode of the PreToolUse prechecks (CGH_HOOK_ANSWERS=1,
#              or `answers = true` under [hooks] in a cgh config file).
#              Instead of pointing the agent at a cgh tool, the hook runs the
#              lookup itself and hands over the result:
#              search_answer   a grep for a name: its definitions, read from
#                              the FTS, and its callers, asked of the live
#                              owner when there is one
#              outline_answer  a full read of an indexed source file: the
#                              file's symbols with their line ranges
#              text_outline    the plan of a large text file cgh does not
#                              index, given when the Read hook denies a full
#                              Read past FULL_READ_ANSWER (retry_after_deny
#                              and remember_deny let the next one through)
#              Both return None when cgh has nothing to add, so the hook stays
#              silent rather than nudging on a search cgh cannot answer.

from __future__ import annotations

import json
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


def enabled(start: Path | None = None) -> bool:
    """Answer mode on or off. CGH_HOOK_ANSWERS=1 or 0 decides; unset, the
    ``answers`` key of the ``[hooks]`` table does, in the repo's
    .codegraph/config.toml first, then in ~/.codegraph/config.toml. The
    files are read on every hook call, so a session that is already running
    takes a change at its next search, where an environment variable only
    reaches sessions started after it was set."""
    return hooks_setting("answers", "CGH_HOOK_ANSWERS", False, start)


def hooks_setting(key: str, env: str, default: bool, start: Path | None) -> bool:
    """A boolean of the ``[hooks]`` table. The environment variable ``env``
    set to 1 or 0 wins; unset, the repo's .codegraph/config.toml decides,
    then ~/.codegraph/config.toml, then ``default``."""
    value = os.environ.get(env, "").strip()
    if value in ("0", "1"):
        return value == "1"
    from codegraph.core.config import GLOBAL_DIR

    root = repo_root(start or Path.cwd())
    paths = [root / ".codegraph" / "config.toml"] if root else []
    for path in (*paths, GLOBAL_DIR / "config.toml"):
        configured = _configured(path, key)
        if configured is not None:
            return configured
    return default


def _configured(path: Path, key: str = "answers") -> bool | None:
    """[hooks] ``key`` from one config file, or None when it does not say.
    A missing or unreadable file says nothing: the default applies."""
    import tomllib

    try:
        with open(path, "rb") as fh:
            hooks = tomllib.load(fh).get("hooks")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = hooks.get(key) if isinstance(hooks, dict) else None
    return value if isinstance(value, bool) else None


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
    # The owner answers hooks with repo-relative paths already; resolving one
    # would anchor it at the hook's working directory instead of the repo.
    if not os.path.isabs(path):
        return path
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


# Read this many definitions before filtering: a generic name (get_by_id on
# every manager) is narrowed down by the search itself, not dropped.
_MAX_CANDIDATES = 50
_CALLABLE_KEYWORDS = frozenset({"def", "function", "func", "fn", "async"})
_TYPE_KEYWORDS = frozenset({"class", "struct", "interface", "type", "enum"})
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|\.(test|spec)\.[^/]+$"
)


def _definitions(conn: sqlite3.Connection, name: str) -> list[tuple]:
    # A method may be stored qualified ("Class.method"): match the bare name too.
    return conn.execute(
        "SELECT kind, name, file_path, start_line, end_line FROM symbols "
        "WHERE (name = ? OR name LIKE ?) AND kind != 'md_section' "
        "ORDER BY name = ? DESC, file_path LIMIT ?",
        (name, f"%.{name}", name, _MAX_CANDIDATES),
    ).fetchall()


def _snake(word: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", word).lower()


def narrow(
    defs: list[tuple], name: str, pattern: str, root: Path, scopes: list[str]
) -> list[tuple]:
    """Keep the definitions the search itself points at, when it says enough.

    Each filter but the first applies only when it leaves something: a hint
    that matches nothing is ignored rather than turning the answer into
    silence.
    - scopes: the directories the search runs in (relative to the root);
      when none of them defines the name, nothing is left;
    - a keyword before the name: def/function keeps callables, class/type
      keeps types;
    - a CamelCase qualifier (WebhookManager.get_by_id): the symbol name or
      its file name carries it;
    - tests: dropped unless the search runs in tests."""

    def keep(rows: list[tuple], test) -> list[tuple]:
        kept = [r for r in rows if test(r)]
        return kept or rows

    def rel(r: tuple) -> str:
        return _rel(r[2], root)

    if scopes:
        # A search confined to directories that define nothing of that name
        # looks for its uses, not its definition: say nothing.
        defs = [
            r
            for r in defs
            if any(rel(r).startswith(s.rstrip("/") + "/") for s in scopes)
        ]
    m = re.search(rf"\b(\w+)\s+{re.escape(name)}\b", pattern)
    if m and m.group(1) in _CALLABLE_KEYWORDS:
        defs = keep(defs, lambda r: r[0] in ("function", "method", "arrow"))
    elif m and m.group(1) in _TYPE_KEYWORDS:
        defs = keep(defs, lambda r: r[0] not in ("function", "method", "arrow"))
    q = re.search(rf"\b([A-Z]\w+)\\?\.{re.escape(name)}\b", pattern)
    if q:
        qual = q.group(1)
        defs = keep(
            defs,
            lambda r: (
                r[1].endswith(f"{qual}.{name}") or _snake(qual) in Path(r[2]).stem
            ),
        )
    if not any(_TEST_PATH_RE.search(s + "/") for s in scopes):
        defs = keep(defs, lambda r: not _TEST_PATH_RE.search(rel(r)))
    return defs


def search_scopes(paths: list[str], cwd: Path, root: Path) -> list[str]:
    """The directories a search runs in, relative to the repo root."""
    scopes = []
    for p in paths:
        d = (cwd / p).resolve()
        if d.is_dir():
            try:
                rel = d.relative_to(root.resolve())
            except ValueError:
                continue
            if str(rel) != ".":
                scopes.append(str(rel))
    return scopes


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


def search_answer(
    root: Path, pattern: str, scopes: list[str] | None = None
) -> str | None:
    conn = _fts(root)
    if conn is None:
        return None
    blocks: list[str] = []
    try:
        names = names_in(pattern)
        for name in names:
            found = _definitions(conn, name)
            if not found:
                continue
            defs = narrow(found, name, pattern, root, scopes or [])
            if not defs:
                continue
            # A file that also defines another name of the same search is the
            # likelier target: lock_subscription|get_for_org puts the manager
            # that has both first.
            others = {r[2] for n in names if n != name for r in _definitions(conn, n)}
            defs.sort(key=lambda r: r[2] not in others)
            lines = [f"`{name}`:"]
            for kind, full, fp, sl, el in defs[:_MAX_DEFS]:
                lines.append(f"  defined  {full} ({kind})  {_rel(fp, root)}:{sl}-{el}")
            if len(defs) > _MAX_DEFS:
                more = f"{len(defs) - _MAX_DEFS}" + (
                    "+" if len(found) == _MAX_CANDIDATES else ""
                )
                lines.append(
                    f"  ({more} more definitions: search in their directory "
                    "or qualify the name, e.g. Class.method)"
                )
            # Callers only for a search on one name, and one that points at a
            # single place: on an alternation they multiply the answer, and on
            # a name defined in many files they mix every definition's callers.
            if len(names) > 1 or len(found) > _MAX_DEFS:
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


def outline_answer(root: Path, target: Path, tail: bool = True) -> str | None:
    """The symbols of an indexed source file with their line ranges, or None
    below a handful of symbols. ``tail=False`` leaves out the closing line on
    reading a part, for a caller that gives its own instruction."""
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
        + (
            "\nIf you need one part, Read it with offset/limit instead of the whole file."
            if tail
            else ""
        )
    )


# --- full Read of a large text file -------------------------------------------

# Past this size, a full Read of a text file is answered with its plan instead
# of run: the study transcripts showed the advisory hint ignored on most full
# reads of files this large.
FULL_READ_ANSWER = 32 * 1024
_SNIFF = 8192
_TEXT_OUTLINE_MAX = 4 * 1024 * 1024
_HEADINGS_MAX = 20
_MIN_HEADINGS = 3
_ANCHORS = 10
_ANCHOR_TEXT = 70
# Lock and generated files repeat the same keys hundreds of times: their
# heading-like lines make no plan, the anchors do.
_GENERATED_NAMES = frozenset(
    {
        "uv.lock", "poetry.lock", "Cargo.lock", "package-lock.json",
        "pnpm-lock.yaml", "yarn.lock",
    }
)  # fmt: skip
_GENERATED_SUFFIXES = (".min.js", ".map")


def is_generated(target: Path) -> bool:
    """True for a lock file or a generated bundle, outlined by anchors only."""
    return target.name in _GENERATED_NAMES or target.name.endswith(_GENERATED_SUFFIXES)


# Lines that open a part of a text file: a markdown heading, a file of a
# patch, an INI or TOML section, a block in tfvars or HCL, a top-level key of
# a JSON object holding an object or a list.
_HEADING_RE = re.compile(
    r"^(#{1,6}\s+\S"
    r"|diff --git "
    r"|\[\[?[^\]\n]+\]\]?\s*$"
    r"|[A-Za-z_][\w.-]*\s*=\s*[\[{]\s*$"
    r'| {0,2}"[^"\n]{1,60}"\s*:\s*[\[{])'
)


def is_binary(target: Path) -> bool:
    """True when the start of the file holds a NUL byte, or cannot be read."""
    try:
        with target.open("rb") as fh:
            return b"\0" in fh.read(_SNIFF)
    except OSError:
        return True


def _anchor(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _ANCHOR_TEXT else text[: _ANCHOR_TEXT - 3] + "..."


def text_outline(target: Path, size: int) -> str:
    """A plan of a text file cgh does not index: its heading-like lines with
    their line numbers when it has a few, else its line count and evenly
    spaced anchors, so a ranged Read can aim at a part."""
    from codegraph.cli.hook_advice import _human_size

    name = target.name
    if size > _TEXT_OUTLINE_MAX:
        return f"{name} ({_human_size(size)}), too large to outline here."
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"{name} ({_human_size(size)})."
    total = len(lines)
    head = f"{name} ({_human_size(size)}, {total:,} lines)"
    headings = (
        []
        if is_generated(target)
        else [(i + 1, ln) for i, ln in enumerate(lines) if _HEADING_RE.match(ln)]
    )
    if len(headings) >= _MIN_HEADINGS:
        step = max(1, -(-len(headings) // _HEADINGS_MAX))
        shown = headings[::step][:_HEADINGS_MAX]
        rows = [f"  {n}  {_anchor(t)}" for n, t in shown]
        if len(shown) < len(headings):
            rows.append(f"  ({len(headings)} heading-like lines in all)")
        return f"{head}, its parts:\n" + "\n".join(rows)
    rows: list[str] = []
    seen: set[int] = set()
    for k in range(_ANCHORS):
        start = k * total // _ANCHORS
        # The first non-blank line at or just after the anchor.
        n = next(
            (j for j in range(start, min(total, start + 5)) if lines[j].strip()),
            start,
        )
        if n < total and n not in seen:
            seen.add(n)
            rows.append(f"  {n + 1}  {_anchor(lines[n])}")
    return f"{head}, lines along the file:\n" + "\n".join(rows)


def _denied_path(root: Path, session_id: str) -> Path:
    from codegraph.core.utils import ensure_dir

    path = root / ".codegraph" / "sessions"
    ensure_dir(path)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "unknown"
    return path / f"read-denied-{safe}.json"


def _load_denied(path: Path) -> list[str]:
    try:
        denied = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in denied if isinstance(p, str)] if isinstance(denied, list) else []


def retry_after_deny(root: Path, session_id: str, file_path: str) -> bool:
    """True when this session's last full Read of the file was denied: the
    agent insists, so this one goes through and the record is cleared. A
    later full Read of the same file is answered again."""
    try:
        path = _denied_path(root, session_id)
        denied = _load_denied(path)
        if file_path not in denied:
            return False
        path.write_text(
            json.dumps([p for p in denied if p != file_path]), encoding="utf-8"
        )
    except OSError:
        return False
    return True


def remember_deny(root: Path, session_id: str, file_path: str) -> bool:
    """Record a denied full Read, so the retry goes through. False when the
    record cannot be written: the caller then lets the Read run, since a deny
    it cannot lift would block the file for the whole session."""
    try:
        path = _denied_path(root, session_id)
        denied = _load_denied(path)
        if file_path not in denied:
            path.write_text(json.dumps([*denied, file_path]), encoding="utf-8")
    except OSError:
        return False
    return True


def full_read_reason(rel_path: str, size: int, plan: str) -> str:
    from codegraph.cli.hook_advice import _human_size

    return (
        f"[cgh] Full Read of {rel_path} ({_human_size(size)}) answered with its "
        f"plan instead:\n{plan}\n"
        "Re-read the part you need with offset/limit. If you need the whole "
        "file, repeat the same Read and it goes through."
    )
