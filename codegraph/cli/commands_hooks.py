# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Claude Code hook subcommands (invoked from .claude/settings.json).

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

from codegraph.cli import hook_advice, hook_answers

# Bare identifier: starts with a letter or underscore, followed by 2+ word
# chars, no regex metachars. Matches names like `user_manager` or `MyClass`
# but skips `.*`, `foo|bar`, `auth_.*`, etc., patterns that wouldn't map
# cleanly onto cgh's symbol search.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")

# Minimum symbols-in-file before the Read precheck nags. Below this, the
# file is likely a config / README / fixture and a full Read is fine.
_MIN_SYMBOLS_FOR_OUTLINE_HINT = 5

# Do not stream-hash an artifact larger than this inside a PreToolUse hook,
# freshness stays "unverified" rather than stalling the Read on a huge file.
_ARTIFACT_HASH_CAP = 25 * 1024 * 1024
# Only nudge to record a summary for a file big enough that re-reading it costs
# real tokens; a tiny icon is not worth caching.
_ARTIFACT_RECORD_MIN = 20 * 1024


def _emit_nudge(message: str) -> None:
    """Deliver an advisory nudge to the model on a PreToolUse hook, then
    exit 0.

    Plain stdout/stderr on exit 0 does NOT reach the model for PreToolUse:
    Claude Code only turns exit-0 output into context for UserPromptSubmit,
    UserPromptExpansion and SessionStart. The channel PreToolUse does
    deliver is `hookSpecificOutput.additionalContext`, emitted as JSON on
    stdout. This stays advisory: no permission decision is set, so the tool
    call is never blocked or prompted.
    """
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": message,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def cmd_hook_precheck_grep(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Grep. Reads the hook payload from stdin, and when
    the pattern looks like a bare identifier delivers a suggestion (via
    hookSpecificOutput.additionalContext) pointing at cgh's symbol-search
    MCP tools. A content-mode Grep with unbounded output also gets a line on
    bounding it, appended to whatever else the hook says. Always exits 0,
    advisory, never blocking, so Claude can still run Grep when it really
    wants to.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)
    pattern = tool_input.get("pattern")
    cwd = Path(payload.get("cwd") or os.getcwd())
    advice = _grep_advice(tool_input, cwd)
    if hook_answers.enabled(cwd):
        root = hook_answers.repo_root(cwd)
        target = tool_input.get("path")
        scopes = (
            hook_answers.search_scopes([target], cwd, root)
            if root and isinstance(target, str)
            else []
        )
        one_file = isinstance(target, str) and hook_answers.files_only([target], cwd)
        answer = (
            hook_answers.search_answer(root, pattern, scopes)
            if root and isinstance(pattern, str) and not one_file
            else _grep_one_file(root, cwd / target)
            if root and one_file
            else None
        )
        if answer or advice:
            _emit_nudge("\n".join(m for m in (answer, advice) if m))
        sys.exit(0)
    if not isinstance(pattern, str) or not _IDENTIFIER_RE.match(pattern):
        if advice:
            _emit_nudge(advice)
        sys.exit(0)

    # The identifier nudge is the same paragraph every time: full once per
    # session, then one line. The size advice is about this call, so it stays.
    nudge = _once_per_session(
        payload,
        cwd,
        "Grep on a bare identifier",
        f"[cgh hook] Grep pattern '{pattern}' looks like a bare identifier. "
        "Prefer cgh MCP tools for symbol search (zero-token server-side execution):\n"
        f"  - symbol_lookup('{pattern}')   exact definition (functions, classes, sections)\n"
        f"  - search_symbols('{pattern}')  fuzzy across the graph\n"
        f"  - find_callers('{pattern}')    incoming CALLS edges (functions only)\n"
        "Override: re-run Grep with a regex containing a metachar (e.g. '\\\\b' or '|') "
        "to confirm a raw text search is what you want.",
    )
    _emit_nudge(nudge + (f"\n{advice}" if advice else ""))


def _grep_one_file(root: Path, target: Path) -> str | None:
    """A Grep inside one named file runs as asked. When that repo file is
    missing from the index (or older there), the live owner indexes it now,
    so the lookups that follow answer from the graph; nothing is parsed in
    the hook, the Grep output already covers this file."""
    try:
        from codegraph.analysis import on_demand

        rel = on_demand.relative(target, root)
        if rel is None:
            return None
        count = _count_symbols(
            root / ".codegraph" / "fts.db", str(target.resolve()), str(rel)
        )
        outcome = on_demand.for_hook(root, target, count, parse=False)
    except Exception:
        return None
    if outcome is None or not outcome.indexed:
        return None
    return (
        f"[cgh] {rel.as_posix()} was not in the index: indexed just now, so "
        "symbol_lookup, file_summary and find_callers cover it."
    )


def _grep_advice(tool_input: dict, cwd: Path) -> str | None:
    """The output-size hint for a Grep call, inside a cgh repo and unless
    turned off. The pure check runs first so most calls read no config."""
    advice = hook_advice.grep_advice(tool_input, cwd)
    if advice is None or not _in_cgh_repo(cwd):
        return None
    return advice if hook_advice.grep_advice_enabled(cwd) else None


# The Grep precheck above only sees the Grep tool. The same lookups also run as
# shell commands through Bash (git grep, grep -rn, rg, sed -n on a source file),
# which is how an agent drifts off cgh without ever touching Grep. The Bash
# precheck below covers that path.
_SHELL_SEARCHERS = frozenset({"rg", "ag", "ack"})
_SHELL_READERS = frozenset({"sed", "cat", "head", "tail", "awk"})
_SHELL_WRAPPERS = frozenset({"sudo", "time", "command", "exec", "nice", "env"})
# Reading one of these through the shell is reading code. Logs, configs and
# data files stay out, so `tail -f owner.log` or `cat pyproject.toml` pass.
_SOURCE_EXTS = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue",
        ".go", ".rs", ".java", ".kt", ".tf", ".rb", ".php", ".cs", ".c", ".h",
        ".cc", ".cpp", ".hpp", ".swift", ".scala",
    }
)  # fmt: skip


def _pipeline_heads(command: str) -> list[list[str]]:
    """The first command of every pipeline in a shell line, tokenized.

    A command that only receives piped input (``... | grep x``) filters another
    command's output rather than searching files, so it is never returned.
    Environment assignments and wrappers like ``time`` are stripped so the real
    program comes first. Unbalanced quotes fall back to a whitespace split:
    this is an advisory heuristic, not a shell parser."""
    heads: list[list[str]] = []
    for chain in re.split(r"&&|\|\||;|\n", command):
        first = chain.split("|", 1)[0].strip()
        if not first:
            continue
        try:
            toks = shlex.split(first)
        except ValueError:
            toks = first.split()
        while toks and (
            toks[0] in _SHELL_WRAPPERS
            or ("=" in toks[0] and not toks[0].startswith("-"))
        ):
            toks.pop(0)
        if toks:
            heads.append(toks)
    return heads


def _is_recursive_grep(args: list[str]) -> bool:
    for a in args:
        if a in ("--recursive", "--dereference-recursive"):
            return True
        if a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a):
            return True
    return False


def _shell_search_hint(command: str) -> str | None:
    """The nudge for a shell command that locates or reads code, else None.
    Heredoc bodies are stripped first: a file written with ``cat <<EOF`` may
    well mention grep without searching anything."""
    for toks in _pipeline_heads(hook_advice.strip_heredocs(command)):
        prog, args = os.path.basename(toks[0]), toks[1:]
        searching = (
            (prog == "git" and args[:1] == ["grep"])
            or (prog in ("grep", "egrep", "fgrep") and _is_recursive_grep(args))
            or prog in _SHELL_SEARCHERS
        )
        if searching:
            label = "git grep" if prog == "git" else prog
            return (
                f"[cgh hook] `{label}` over the repo to locate code. cgh answers "
                "this server-side with exact file:line, at near-zero token cost:\n"
                "  - pattern_search(pattern, glob?)   text or regex, structured hits\n"
                "  - symbol_lookup(name) / search_symbols(query)   a definition\n"
                "  - find_callers(fn) / find_callees(fn)   call sites\n"
                "Then Read only the returned lines. Shell search stays right for "
                "logs and files cgh does not index."
            )
        if prog == "find" and any(
            a in ("-name", "-iname", "-path", "-ipath") for a in args
        ):
            return (
                "[cgh hook] `find` to locate files. Prefer search_symbols(query) "
                "for code by name, or architecture_overview() / domain_map(keyword) "
                "for structure."
            )
        if prog in _SHELL_READERS:
            sources = [a for a in args if Path(a).suffix.lower() in _SOURCE_EXTS]
            if sources:
                return (
                    f"[cgh hook] `{prog}` to read {sources[0]}. Prefer "
                    "symbol_lookup(name) for the exact line range, then Read with "
                    "offset/limit (or file_summary for an outline)."
                )
    return None


def _shell_answer(command: str, cwd: Path) -> str | None:
    """Answer mode for a shell line: the lookup a code search or a full read
    of a source file was after, run here. A leading ``cd`` moves the base
    for the relative paths that follow it. A search over files named one by
    one gets no answer: the agent already knows where to look."""
    command = hook_advice.strip_heredocs(command)
    for toks in _pipeline_heads(command):
        prog, args = os.path.basename(toks[0]), toks[1:]
        if prog == "cd" and args:
            cwd = (cwd / args[0]).resolve()
            continue
        searching = (
            (prog == "git" and args[:1] == ["grep"])
            or prog in ("grep", "egrep", "fgrep")
            or prog in _SHELL_SEARCHERS
        )
        root = hook_answers.repo_root(cwd)
        if root is None:
            return None
        if searching:
            pattern, paths = hook_answers.search_parts(prog, args)
            if not pattern or hook_answers.files_only(paths, cwd):
                return None
            scopes = hook_answers.search_scopes(paths, cwd, root)
            return hook_answers.search_answer(root, pattern, scopes)
        # cat reads the whole file; sed -n, head and tail already pick a range.
        if prog == "cat":
            sources = [a for a in args if Path(a).suffix.lower() in _SOURCE_EXTS]
            if sources:
                return hook_answers.outline_answer(root, cwd / sources[0])
    return None


def _in_cgh_repo(start: Path) -> bool:
    return any((p / ".codegraph").is_dir() for p in (start, *start.parents))


_HINT_LABEL_RE = re.compile(r"`([^`]+)`")


def _once_per_session(payload: dict, cwd: Path, kind: str, full: str) -> str:
    """The full static hint the first time a session meets it, a one-line
    reminder afterwards. Without a session id or a cgh root, always the full
    hint."""
    from codegraph.core.config import find_codegraph_root

    session_id = payload.get("session_id")
    root = find_codegraph_root(cwd)
    if not isinstance(session_id, str) or not session_id or root is None:
        return full
    if not hook_advice.seen_before(root, session_id, kind):
        return full
    return (
        f"[cgh hook] {kind} again: locate code with symbol_lookup / "
        "pattern_search first (full hint earlier in this session)."
    )


def cmd_hook_precheck_bash(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Bash. When the command searches or reads code through
    the shell (git grep, grep -r, rg, find -name, sed or cat on a source file)
    inside a cgh repo, points at the cgh tool that answers it. Always exits 0,
    advisory, never blocking: the command still runs.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        sys.exit(0)
    cwd = payload.get("cwd") or os.getcwd()
    if not _in_cgh_repo(Path(cwd)):
        sys.exit(0)

    if hook_answers.enabled(Path(cwd)):
        answer = _shell_answer(command, Path(cwd))
        if answer:
            _emit_nudge(answer)
    else:
        hint = _shell_search_hint(command)
        if hint:
            m = _HINT_LABEL_RE.search(hint)
            kind = f"`{m.group(1)}`" if m else "shell search"
            _emit_nudge(_once_per_session(payload, Path(cwd), kind, hint))
    # Nothing to say about locating code: maybe about the output size.
    if hook_advice.bash_advice_enabled(Path(cwd)):
        advice = hook_advice.bash_advice(command, Path(cwd))
        if advice:
            _emit_nudge(advice)
    sys.exit(0)


def cmd_hook_compact_hint(args: argparse.Namespace) -> None:
    """
    UserPromptSubmit hook. When the session transcript has grown past a size
    threshold since its last compaction, says so once (to the user and the
    model) and suggests /compact, or a cgh checkpoint then /clear. Never
    blocks the prompt: every failure is silent and exits 0.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        transcript = payload.get("transcript_path")
        session_id = payload.get("session_id")
        if not isinstance(transcript, str) or not isinstance(session_id, str):
            sys.exit(0)
        from codegraph.core.config import find_codegraph_root

        cwd = Path(payload.get("cwd") or os.getcwd())
        root = find_codegraph_root(cwd)
        if root is None or not hook_advice.compact_hint_enabled(cwd):
            sys.exit(0)
        hint = hook_advice.compact_hint(root, session_id, Path(transcript))
    except Exception:
        sys.exit(0)
    if hint:
        json.dump(
            {
                "systemMessage": hint,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": hint,
                },
            },
            sys.stdout,
        )
    sys.exit(0)


def _emit_artifact_recall(target: Path, rel_path: str, body: str) -> None:
    """Surface a saved summary for an opaque file, tagged with whether the file
    still matches the summary. Never blocks the Read."""
    from codegraph.cli.commands_artifact import (
        parse_stored_sha,
        sha256_file,
        strip_marker,
    )

    summary = strip_marker(body)
    stored = parse_stored_sha(body)
    state = "unverified"
    try:
        size = target.stat().st_size
    except OSError:
        size = None
    if stored and size is not None and size <= _ARTIFACT_HASH_CAP:
        try:
            state = "fresh" if sha256_file(target) == stored else "stale"
        except OSError:
            state = "unverified"

    if state == "stale":
        _emit_nudge(
            f"[cgh hook] '{rel_path}' has CHANGED since cgh last summarized it. "
            "The saved summary below is now out of date, re-inspect and save a new one "
            f"(knowledge_record(..., kind='note', tags='artifact', file_refs=['{rel_path}']) "
            "or `cgh artifact note`):\n\n"
            f"{summary}"
        )
    elif state == "fresh":
        _emit_nudge(
            f"[cgh hook] cgh already inspected '{rel_path}' and the file is unchanged. "
            "Use this saved summary instead of re-reading; re-inspect only for detail beyond it:\n\n"
            f"{summary}"
        )
    else:
        _emit_nudge(
            f"[cgh hook] cgh has a saved summary of '{rel_path}' (freshness unverified). "
            "Use it if it suffices; re-inspect for anything beyond it:\n\n"
            f"{summary}"
        )


def _artifact_precheck(
    repo_root: Path,
    target: Path,
    rel_path: str,
    abs_path: str,
    nudge_record: bool = True,
) -> None:
    """Read hook branch for files cgh cannot parse (images, legacy office
    docs). Surfaces a saved summary if one exists, else nudges to record one
    after the inspection so the next read is free. ``nudge_record=False`` only
    surfaces a saved summary and returns when there is none: used for the
    documents cgh parses now, whose summaries were saved when they were still
    opaque. Advisory and best-effort throughout: a missing DB or a bad row must
    never break the Read."""
    db = repo_root / ".codegraph" / "call_log.db"
    hit_body: str | None = None
    if db.is_file():
        try:
            from codegraph.core.utils import ro_sqlite_uri

            conn = sqlite3.connect(ro_sqlite_uri(db), uri=True, timeout=0.5)
            rows = conn.execute(
                "SELECT body, file_refs FROM knowledge "
                "WHERE kind='note' AND tags LIKE '%artifact%' AND file_refs LIKE ? "
                "AND superseded_by IS NULL ORDER BY ts DESC LIMIT 5",
                (f"%{rel_path}%",),
            ).fetchall()
            conn.close()
            for body, refs in rows:
                parts = [p for p in (refs or "").split(",") if p]
                if rel_path in parts or abs_path in parts:
                    hit_body = body
                    break
        except sqlite3.Error:
            hit_body = None

    if hit_body is not None:
        _emit_artifact_recall(target, rel_path, hit_body)
        return

    if not nudge_record:
        return

    # No saved summary. Only prompt to record for a file big enough that a
    # re-read costs real tokens, and only advisory.
    try:
        size = target.stat().st_size
    except OSError:
        return
    if size < _ARTIFACT_RECORD_MIN:
        return
    ext = target.suffix.lower().lstrip(".")
    _emit_nudge(
        f"[cgh hook] '{rel_path}' is a {ext} cgh can't parse, and has no saved summary. "
        "After you inspect it, save what you learned so the next read is cheap:\n"
        f"  knowledge_record(title='{target.name}', body='<summary>', kind='note', "
        f"tags='artifact', file_refs=['{rel_path}'])\n"
        f"  or: cgh artifact note {rel_path} --summary '<summary>'"
    )


# Read handles these by page or cell, where offset/limit do not apply.
_READ_ADVICE_SKIP = frozenset({".ipynb"})


def _read_advice(target: Path, repo_root: Path) -> None:
    """Emit the hint for a full Read of a large file cgh does not index, or
    return when there is none. Advisory: any failure returns silently."""
    if target.suffix.lower() in _READ_ADVICE_SKIP:
        return
    try:
        if not hook_advice.read_advice_enabled(repo_root):
            return
        advice = hook_advice.read_advice(target)
    except Exception:
        return
    if advice:
        _emit_nudge(advice)


def _ranges_advice(
    target: Path,
    repo_root: Path,
    fts_db: Path,
    abs_path: str,
    rel_path: str,
    parsed: list | None = None,
) -> None:
    """Emit the line-ranges hint for a full Read of a large indexed file, or
    return when there is none. ``parsed`` holds the rows of an in-memory
    parse, used in place of the index. Advisory: any failure returns
    silently."""
    if target.suffix.lower() in _READ_ADVICE_SKIP:
        return
    try:
        size = target.stat().st_size
        if size < hook_advice.LARGE_FILE:
            return
        if not hook_advice.read_advice_enabled(repo_root):
            return
        rows = _symbol_ranges(fts_db, abs_path, rel_path, parsed)
        advice = hook_advice.ranges_advice(target, size, rows)
    except Exception:
        return
    if advice:
        _emit_nudge(advice)


def _symbol_ranges(
    fts_db: Path, abs_path: str, rel_path: str, parsed: list | None = None
) -> list[tuple[str, int, int]]:
    """The (name, start, end) rows of the file: from ``parsed`` when given,
    else from the FTS index."""
    if parsed is not None:
        return [(n, s, e) for _, n, s, e in parsed[: hook_advice.RANGES_ROWS]]
    from codegraph.core.utils import ro_sqlite_uri

    conn = sqlite3.connect(ro_sqlite_uri(fts_db), uri=True, timeout=0.5)
    try:
        return conn.execute(
            "SELECT name, start_line, end_line FROM symbols "
            "WHERE file_path IN (?, ?) ORDER BY start_line LIMIT ?",
            (abs_path, rel_path, hook_advice.RANGES_ROWS),
        ).fetchall()
    finally:
        conn.close()


def _count_symbols(fts_db: Path, abs_path: str, rel_path: str) -> int:
    from codegraph.core.utils import ro_sqlite_uri

    conn = sqlite3.connect(ro_sqlite_uri(fts_db), uri=True, timeout=0.5)
    try:
        row = conn.execute(
            "SELECT count(*) FROM symbols WHERE file_path IN (?, ?)",
            (abs_path, rel_path),
        ).fetchone()
    finally:
        conn.close()
    return (row or [0])[0]


def _on_demand(
    repo_root: Path,
    target: Path,
    fts_db: Path,
    abs_path: str,
    rel_path: str,
    symbol_count: int,
    answer_mode: bool,
) -> tuple[int, list | None, str]:
    """Make a file the index lacks (or holds in an older version) answerable
    before the Read hook decides: the live owner indexes it, or with no
    owner it is parsed in memory. Returns the symbol count, the rows of an
    in-memory parse (None when the FTS holds them) and the outline's source
    line. Only where the answer can change: answer mode, or a file large
    enough for the ranges hint. Any failure keeps what the index said."""
    unchanged = (symbol_count, None, hook_answers.FROM_GRAPH)
    try:
        if not answer_mode and target.stat().st_size < hook_advice.LARGE_FILE:
            return unchanged
        from codegraph.analysis import on_demand

        outcome = on_demand.for_hook(repo_root, target, symbol_count)
        if outcome is None:
            return unchanged
        if outcome.indexed:
            count = _count_symbols(fts_db, abs_path, rel_path)
            return count, None, hook_answers.INDEXED_NOW
        rows = outcome.rows or []
        return len(rows), rows, hook_answers.PARSED_NOW
    except Exception:
        return unchanged


def _emit_deny(reason: str) -> None:
    """Deny the tool call with ``reason``, which Claude Code hands to the
    model in place of the tool's output, then exit 0."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def _answer_large_read(
    payload: dict,
    target: Path,
    repo_root: Path,
    fts_db: Path,
    abs_path: str,
    rel_path: str,
    symbol_count: int,
    parsed: list | None = None,
    source: str = hook_answers.FROM_GRAPH,
) -> None:
    """Answer mode for a full Read of a text file past FULL_READ_ANSWER: deny
    it with the file's plan, or return when the Read should run. It runs when
    the file is small, binary or a notebook, when there is no session to
    remember the deny in, and on the first retry after a deny. ``parsed``
    holds the rows of an in-memory parse, used in place of the index."""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if target.suffix.lower() in _READ_ADVICE_SKIP:
        return
    try:
        size = target.stat().st_size
    except OSError:
        return
    if size <= hook_answers.FULL_READ_ANSWER or not target.is_file():
        return
    if hook_answers.is_binary(target):
        return
    if hook_answers.retry_after_deny(repo_root, session_id, abs_path):
        # The plan is already in the transcript from the deny: say nothing.
        sys.exit(0)
    plan: str | None = None
    if symbol_count:
        if symbol_count >= _MIN_SYMBOLS_FOR_OUTLINE_HINT:
            plan = (
                hook_answers.format_outline(rel_path, parsed, tail=False, source=source)
                if parsed is not None
                else hook_answers.outline_answer(
                    repo_root, target, tail=False, source=source
                )
            )
        if plan is None:
            try:
                rows = _symbol_ranges(fts_db, abs_path, rel_path, parsed)
                plan = hook_advice.ranges_advice(target, size, rows, bare=True)
            except Exception:
                plan = None
    if plan is None:
        plan = hook_answers.text_outline(target, size)
    if not hook_answers.remember_deny(repo_root, session_id, abs_path):
        return
    _emit_deny(hook_answers.full_read_reason(rel_path, size, plan))


def cmd_hook_precheck_read(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Read. When the file is indexed in cgh's FTS and the
    Read is a full read (no offset/limit), suggest file_outline / symbols_in_file
    first, both return structured summaries for a fraction of the tokens of
    a raw Read. A full read of a large file the index does not hold (JSON,
    a lock file, a log) gets a one-line hint to read a part of it instead,
    and one of a large indexed file a line with its main symbols' ranges.
    The suggestion is delivered via hookSpecificOutput.additionalContext.
    With hook answers on, a full Read of a text file past 32 KB is denied
    instead, the reason carrying the file's plan; repeating the Read lets it
    through. A file the index lacks or holds in an older version is first
    indexed by the live owner, or parsed in memory without one (always for a
    file outside the repo), then answered the same way; a gitignored file is
    left alone. That step gives up past a 300 ms budget. Always exits 0.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        sys.exit(0)

    # Sliced reads mean the caller already knows the range they want, skip.
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        sys.exit(0)

    target = Path(file_path)
    if not target.is_absolute():
        cwd = payload.get("cwd") or os.getcwd()
        target = Path(cwd) / target

    # An image read earlier in this session is still in the conversation:
    # say so instead of letting the same capture be paid for twice.
    from codegraph.cli.hook_images import reread_hint

    image_hint = reread_hint(payload, target)
    if image_hint:
        _emit_nudge(image_hint)

    # Walk up to find the nearest .codegraph dir; that's the repo cgh
    # considers root for this file. Stops at filesystem root.
    repo_root: Path | None = None
    for parent in [target.parent, *target.parents]:
        if (parent / ".codegraph" / "fts.db").is_file():
            repo_root = parent
            break
    # A file outside every cgh repo (a scratchpad, another checkout) read
    # from a session in one: it is only ever parsed in memory, below.
    outside = repo_root is None
    if repo_root is None:
        repo_root = hook_answers.repo_root(Path(payload.get("cwd") or os.getcwd()))
        if repo_root is None:
            sys.exit(0)

    # cgh's FTS stores file_path as the absolute path on disk (see
    # codegraph/fts.py). Older indexes may have stored relpaths, so try
    # both for forward/backward compatibility.
    try:
        abs_path = str(target.resolve())
        rel_path = (
            abs_path
            if outside
            else str(target.resolve().relative_to(repo_root.resolve()))
        )
    except ValueError:
        sys.exit(0)

    # Files cgh cannot parse into the graph (images, legacy office docs) carry
    # no symbols, so the outline hint below never fires for them. Give them
    # their own treatment instead: surface a saved summary so an expensive
    # re-read is skipped, or nudge to save one after the inspection. This
    # replaces the symbol path for these files, it does not run in addition to
    # it. Documents cgh parses (pdf, docx, xlsx, pptx) take the outline path,
    # after a summary saved back when they were opaque, if there is one.
    try:
        from codegraph.cli.commands_artifact import ARTIFACT_EXTS, PARSED_DOC_EXTS
    except Exception:
        ARTIFACT_EXTS = PARSED_DOC_EXTS = frozenset()
    suffix = target.suffix.lower()
    if suffix in PARSED_DOC_EXTS:
        try:
            _artifact_precheck(
                repo_root, target, rel_path, abs_path, nudge_record=False
            )
        except Exception:
            pass  # advisory: fall through to the outline hint
    if suffix in ARTIFACT_EXTS:
        try:
            _artifact_precheck(repo_root, target, rel_path, abs_path)
        except Exception:
            # Advisory hook: a cache miss or a malformed row must never break
            # a Read. _emit_nudge raises SystemExit(0), which is not caught here.
            pass
        sys.exit(0)

    fts_db = repo_root / ".codegraph" / "fts.db"
    try:
        from codegraph.core.utils import ro_sqlite_uri

        conn = sqlite3.connect(ro_sqlite_uri(fts_db), uri=True, timeout=0.5)
        row = conn.execute(
            "SELECT count(*) FROM symbols WHERE file_path IN (?, ?)",
            (abs_path, rel_path),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        sys.exit(0)

    symbol_count = (row or [0])[0]
    answer_mode = hook_answers.enabled(Path(repo_root))
    parsed, source = None, hook_answers.FROM_GRAPH
    if suffix not in PARSED_DOC_EXTS:
        symbol_count, parsed, source = _on_demand(
            repo_root, target, fts_db, abs_path, rel_path, symbol_count, answer_mode
        )
    if outside and not (parsed and symbol_count):
        sys.exit(0)
    # Answer mode denies a full Read of a large text file and hands back its
    # plan; parsed documents (pdf, docx...) are binaries and keep the hint.
    if answer_mode and suffix not in PARSED_DOC_EXTS:
        _answer_large_read(
            payload,
            target,
            repo_root,
            fts_db,
            abs_path,
            rel_path,
            symbol_count,
            parsed,
            source,
        )
    if symbol_count == 0 and suffix not in PARSED_DOC_EXTS:
        _read_advice(target, repo_root)

    if answer_mode and symbol_count >= _MIN_SYMBOLS_FOR_OUTLINE_HINT:
        answer = (
            hook_answers.format_outline(rel_path, parsed, source=source)
            if parsed is not None
            else hook_answers.outline_answer(repo_root, target, source=source)
        )
        if answer:
            _emit_nudge(answer)
    # Answer mode gave no outline (or is off): a large file gets its main
    # line ranges in one line, in place of the longer tool list below.
    if symbol_count and suffix not in PARSED_DOC_EXTS:
        _ranges_advice(target, repo_root, fts_db, abs_path, rel_path, parsed)
    # The tools below only know indexed files: none for an in-memory parse.
    if (
        symbol_count < _MIN_SYMBOLS_FOR_OUTLINE_HINT
        or answer_mode
        or parsed is not None
    ):
        sys.exit(0)

    is_markdown = suffix in (".md", ".markdown") or suffix in PARSED_DOC_EXTS
    if is_markdown:
        hint = (
            f"  - doc_outline('{rel_path}')   heading tree, cheap structural summary\n"
            f"  - search_docs('<keyword>')    BM25 search across indexed docs\n"
        )
    else:
        hint = (
            f"  - subgraph('{rel_path}')      file + neighbors (imports, callers, classes)\n"
            f"  - imports_of('{rel_path}')    what this file pulls in\n"
            f"  - search_symbols('<name>')    if you're after a specific definition\n"
        )

    just_now = " just now" if source == hook_answers.INDEXED_NOW else ""
    _emit_nudge(
        f"[cgh hook] '{rel_path}' is indexed by cgh{just_now} ({symbol_count} symbols). "
        "Before a full Read, consider:\n"
        f"{hint}"
        "Then Read with offset/limit on the range you actually need. "
        "If you really want the whole file, ignore this and proceed."
    )


def cmd_hook_precheck_agent(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for the sub-agent tool (Agent, formerly Task). Appends
    one line to the sub-agent's brief telling it to locate code with the
    cgh tools before Read or Grep (opt out with [hooks] agent_rule = false
    or CGH_HOOK_AGENT_RULE=0). With CGH_AGENT_LOCATIONS=1 it also appends
    the code locations cgh finds for the brief. Silent outside a cgh repo.
    Never blocks.
    """
    from codegraph.cli import agent_brief

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    tool_input = payload.get("tool_input")
    root = hook_answers.repo_root(Path(payload.get("cwd") or os.getcwd()))
    if not isinstance(tool_input, dict) or root is None:
        sys.exit(0)
    updated = agent_brief.agent_brief(root, tool_input)
    if updated:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": updated,
                }
            },
            sys.stdout,
        )
    sys.exit(0)
