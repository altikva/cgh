# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-22
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Advisory hints that keep a Claude Code session small.
#              bash_advice   a one-line narrower form for a shell command
#                            known to print a lot (pytest without -q, git log
#                            without -n, docker logs without --tail...)
#              compact_hint  once per size threshold, a line saying the
#                            session transcript is large: /compact, or a cgh
#                            checkpoint then /clear
#              read_advice   before a full Read of a large file cgh does
#                            not index, a line on reading only a part of it
#              All only return text: they never block, never run a
#              subprocess, and return None when they have nothing to say.
#              Opt out with [hooks] bash_advice = false / compact_hint = false
#              / read_advice = false in a cgh config file, or
#              CGH_HOOK_BASH_ADVICE=0 / CGH_HOOK_COMPACT_HINT=0 /
#              CGH_HOOK_READ_ADVICE=0.

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from codegraph.cli import hook_answers

_WRAPPERS = frozenset({"sudo", "time", "command", "exec", "nice", "env"})
# Runners in front of the real program: `uv run pytest`, `python -m pytest`.
_RUNNERS = (
    ("uv", "run"),
    ("poetry", "run"),
    ("pipenv", "run"),
    ("pdm", "run"),
    ("hatch", "run"),
    ("pnpm", "exec"),
    ("python", "-m"),
    ("python3", "-m"),
)
_SINGLE_RUNNERS = frozenset({"uvx", "npx", "bunx"})
# A heredoc body is data fed to a program, not commands to advise on.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
# `> file` sends the output to a file, not to the transcript. `2>&1` alone
# does not.
_REDIRECT_RE = re.compile(r"(?<![0-9&])>{1,2}\s*[^&\s]|(?<![0-9])1>\s*[^&\s]|&>")
# Files cgh indexes: reading them whole is the Read precheck's business.
_INDEXED_EXTS = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue",
        ".go", ".rs", ".java", ".kt", ".tf", ".rb", ".php", ".cs", ".c", ".h",
        ".cc", ".cpp", ".hpp", ".swift", ".scala", ".md",
    }
)  # fmt: skip
# A `cat` or a full Read below this size is not worth a hint. Bash outputs
# averaged 3.8 KB in a measured run; 32 KB is about 8k tokens kept in every
# later request.
LARGE_FILE = 32 * 1024
# Lines are counted only up to this size: past it the size alone says enough.
_COUNT_LINES_MAX = 4 * 1024 * 1024
_NAME_MAX = 60
_JS_PMS = frozenset({"npm", "pnpm", "yarn"})
_JS_CHATTY = frozenset({"install", "i", "ci", "add", "build", "run"})
_JS_QUIET = frozenset({"--silent", "-s", "--quiet", "-q", "--reporter=silent"})
_FIND_FILTERS = frozenset(
    {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-newer", "-maxdepth"}
)
_WHOLE_TREE = frozenset({".", "./", "/", "~", "..", "$HOME", "~/"})


def bash_advice_enabled(start: Path | None = None) -> bool:
    return hooks_flag("bash_advice", "CGH_HOOK_BASH_ADVICE", start)


def compact_hint_enabled(start: Path | None = None) -> bool:
    return hooks_flag("compact_hint", "CGH_HOOK_COMPACT_HINT", start)


def read_advice_enabled(start: Path | None = None) -> bool:
    return hooks_flag("read_advice", "CGH_HOOK_READ_ADVICE", start)


def hooks_flag(key: str, env: str, start: Path | None) -> bool:
    """On by default; off with ``env``=0 or ``[hooks] key = false``."""
    return hook_answers.hooks_setting(key, env, True, start)


def strip_heredocs(command: str) -> str:
    """The shell line without its heredoc bodies: text written into a file
    or fed to a program is data, never a command to advise on."""
    return _HEREDOC_RE.sub("", command)


def _segments(command: str) -> list[tuple[list[str], bool]]:
    """Each command of a shell line, tokenized, with whether its output is
    already narrowed (piped into another program or redirected to a file).
    Wrappers, environment assignments and runners are stripped so the real
    program comes first."""
    out: list[tuple[list[str], bool]] = []
    for chain in re.split(r"&&|\|\||;|\n", strip_heredocs(command)):
        first, piped, _ = chain.partition("|")
        first = first.strip()
        if not first:
            continue
        narrowed = bool(piped) or bool(_REDIRECT_RE.search(first))
        try:
            toks = shlex.split(first)
        except ValueError:
            toks = first.split()
        while toks and (
            toks[0] in _WRAPPERS or ("=" in toks[0] and not toks[0].startswith("-"))
        ):
            toks.pop(0)
        changed = True
        while changed and toks:
            changed = False
            if os.path.basename(toks[0]) in _SINGLE_RUNNERS:
                toks = toks[1:]
                changed = True
            elif len(toks) > 1 and (os.path.basename(toks[0]), toks[1]) in _RUNNERS:
                toks = toks[2:]
                changed = True
        if toks:
            out.append((toks, narrowed))
    return out


def _short_flag(args: list[str], letter: str) -> bool:
    return any(
        a.startswith("-") and not a.startswith("--") and letter in a[1:] for a in args
    )


def _pytest(args: list[str]) -> str | None:
    if _short_flag(args, "q") or any(
        a in ("--quiet",) or a.startswith("--tb=") or a == "--tb" for a in args
    ):
        return None
    return (
        "[cgh hook] pytest without -q prints every test and full tracebacks. "
        "Try `pytest -q --tb=short`, or add `| tail -20` for the summary only."
    )


def _git_log(args: list[str]) -> str | None:
    for a in args:
        if a in ("-n", "--max-count") or a.startswith(("--max-count=", "--oneline")):
            return None
        if re.fullmatch(r"-n?\d+", a) or ".." in a:
            return None
        if a.startswith(("--since", "--after", "--until", "--before")):
            return None
    return (
        "[cgh hook] git log without a limit prints the whole history. "
        "Try `git log --oneline -n 20`, or a range like `main..HEAD`."
    )


def _js(pm: str, args: list[str]) -> str | None:
    verb = args[0] if args else ("install" if pm == "yarn" else "")
    if verb not in _JS_CHATTY:
        return None
    if verb == "run" and (len(args) < 2 or args[1] != "build"):
        return None
    if any(a in _JS_QUIET or a.startswith("--loglevel") for a in args):
        return None
    return (
        f"[cgh hook] `{pm} {verb}` prints a lot of progress output. "
        f"Try `{pm} {verb} --silent`, or add `| tail -20`."
    )


def _find(args: list[str]) -> str | None:
    if any(a in _FIND_FILTERS for a in args):
        return None
    paths = [a for a in args if not a.startswith(("-", "(", "!", ")"))][:1]
    if paths and paths[0] not in _WHOLE_TREE:
        return None
    return (
        "[cgh hook] find over the whole tree lists every file. "
        "Add `-maxdepth 2` or a `-name` filter, or `| head -50`."
    )


def _ls(args: list[str]) -> str | None:
    if not (_short_flag(args, "R") or "--recursive" in args):
        return None
    return (
        "[cgh hook] ls -R lists every file of the tree. Try `ls` on one "
        "directory, `tree -L 2`, or add `| head -50`."
    )


def _cat(args: list[str], cwd: Path) -> str | None:
    for a in args:
        if a.startswith("-") or Path(a).suffix.lower() in _INDEXED_EXTS:
            continue
        try:
            size = (cwd / a).stat().st_size
        except (OSError, ValueError):
            continue
        if size >= LARGE_FILE:
            return (
                f"[cgh hook] `cat` of a {size // 1024} KB file stays in every "
                "later request. Try `head -50`, `tail -50`, or `grep -n` for "
                "the part you need."
            )
    return None


def _logs(prog: str, args: list[str]) -> str | None:
    if prog == "docker" and args[:2] == ["compose", "logs"]:
        rest = args[2:]
    elif prog in ("docker", "podman", "kubectl") and args[:1] == ["logs"]:
        rest = args[1:]
    else:
        return None
    # kubectl's -n is the namespace; docker's -n is --tail.
    limits = ("--tail", "--since") if prog == "kubectl" else ("--tail", "--since", "-n")
    if any(a.startswith(limits) for a in rest):
        return None
    label = "docker compose logs" if args[:1] == ["compose"] else f"{prog} logs"
    return (
        f"[cgh hook] `{label}` without --tail prints the whole log. "
        f"Try `{label} --tail 50`."
    )


def bash_advice(command: str, cwd: Path) -> str | None:
    """The narrower form of a chatty command, or None when the line is
    already narrow (piped, redirected, limited) or not a known chatty one."""
    for toks, narrowed in _segments(command):
        if narrowed:
            continue
        prog, args = os.path.basename(toks[0]), toks[1:]
        if prog == "cd" and args:
            cwd = cwd / args[0]
            continue
        hint = None
        if prog in ("pytest", "py.test"):
            hint = _pytest(args)
        elif prog == "git" and args[:1] == ["log"]:
            hint = _git_log(args[1:])
        elif prog in _JS_PMS:
            hint = _js(prog, args)
        elif prog == "find":
            hint = _find(args)
        elif prog == "ls":
            hint = _ls(args)
        elif prog == "cat":
            hint = _cat(args, cwd)
        elif prog in ("docker", "podman", "kubectl"):
            hint = _logs(prog, args)
        if hint:
            return hint
    return None


# --- read advice ---------------------------------------------------------------


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size // 1024} KB"


def _line_count(path: Path, size: int) -> int | None:
    """Newlines in the file, read in chunks, or None past the size cap or
    on a read error."""
    if size > _COUNT_LINES_MAX:
        return None
    count = 0
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                count += chunk.count(b"\n")
    except OSError:
        return None
    return count


def read_advice(target: Path) -> str | None:
    """A one-line hint for a full Read of a large file cgh does not index
    (JSON, a lock file, a log, generated code), else None. The caller has
    already checked that the read is not ranged and the file not indexed."""
    try:
        size = target.stat().st_size
    except OSError:
        return None
    if size < LARGE_FILE or not target.is_file():
        return None
    name = target.name
    if len(name) > _NAME_MAX:
        name = name[: _NAME_MAX - 3] + "..."
    lines = _line_count(target, size)
    what = _human_size(size) + (f", {lines:,} lines" if lines is not None else "")
    return (
        f"[cgh hook] {name} ({what}) is not indexed by cgh, and a full Read "
        "stays in every later request. Read a range with offset/limit, Grep "
        "for a pattern, or `head -n 50` it."
    )


# --- compaction hint ----------------------------------------------------------

# Transcript sizes, counted from the last compaction, at which the hint fires
# once each. Measured on 150 local Claude Code transcripts: the first request
# of a session holds a median 62k tokens, and the context then grows by one
# token per 16 transcript bytes or so (the transcript also keeps tool results
# and metadata the request does not). A proxy saw requests start at 344 KB,
# about 5.5 request bytes per token, so a 400 KB request is 73k to 83k tokens,
# the median context at a 0.5 MB transcript. 1.5 MB is a median 157k tokens,
# just before a 200k window compacts on its own; 3 MB is 264k, for sessions
# on a larger window.
COMPACT_THRESHOLDS = (500_000, 1_500_000, 3_000_000)
_BOUNDARY = b'"subtype":"compact_boundary"'
_CHUNK = 1 << 20


def _state_path(root: Path, session_id: str) -> Path:
    from codegraph.core.utils import ensure_dir

    path = root / ".codegraph" / "sessions"
    ensure_dir(path)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "unknown"
    return path / f"compact-{safe}.json"


def _last_boundary(transcript: Path, start: int, end: int) -> int | None:
    """Offset of the last compaction marker written between start and end,
    read in chunks so a large transcript is never loaded at once."""
    found = None
    overlap = len(_BOUNDARY)
    with transcript.open("rb") as fh:
        pos = max(0, start - overlap)
        while pos < end:
            fh.seek(pos)
            chunk = fh.read(min(_CHUNK, end - pos))
            if not chunk:
                break
            i = chunk.rfind(_BOUNDARY)
            if i >= 0:
                found = pos + i
            if pos + len(chunk) >= end:
                break
            pos += len(chunk) - overlap
    return found


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def compact_hint(root: Path, session_id: str, transcript: Path) -> str | None:
    """The hint for a transcript that just crossed a threshold, once per
    threshold and per compaction, else None. Only the bytes appended since
    the last call are scanned for a compaction marker."""
    try:
        size = transcript.stat().st_size
    except OSError:
        return None
    path = _state_path(root, session_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    scanned = int(state.get("scanned", 0))
    boundary = int(state.get("boundary", 0))
    fired = [t for t in state.get("fired", []) if isinstance(t, int)]
    if size < scanned:  # a rewritten transcript: start over
        scanned, boundary, fired = 0, 0, []
    if size > scanned:
        last = _last_boundary(transcript, scanned, size)
        if last is not None and last > boundary:
            boundary, fired = last, []
    live = size - boundary
    crossed = [t for t in COMPACT_THRESHOLDS if live >= t]
    new = [t for t in crossed if t not in fired]
    path.write_text(
        json.dumps({"scanned": size, "boundary": boundary, "fired": crossed}),
        encoding="utf-8",
    )
    if not new:
        return None
    return (
        f"[cgh] This session's transcript is past {_mb(max(new))} since the "
        "last compaction, and every request resends it. Consider /compact, or "
        "a cgh checkpoint then /clear."
    )
