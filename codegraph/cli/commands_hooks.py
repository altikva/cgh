# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Claude Code hook subcommands (invoked from .claude/settings.json).

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

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
    MCP tools. Always exits 0, advisory, never blocking, so Claude can still
    run Grep when it really wants to.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not _IDENTIFIER_RE.match(pattern):
        sys.exit(0)

    _emit_nudge(
        f"[cgh hook] Grep pattern '{pattern}' looks like a bare identifier. "
        "Prefer cgh MCP tools for symbol search (zero-token server-side execution):\n"
        f"  - symbol_lookup('{pattern}')   exact definition (functions, classes, sections)\n"
        f"  - search_symbols('{pattern}')  fuzzy across the graph\n"
        f"  - find_callers('{pattern}')    incoming CALLS edges (functions only)\n"
        "Override: re-run Grep with a regex containing a metachar (e.g. '\\\\b' or '|') "
        "to confirm a raw text search is what you want."
    )


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
    repo_root: Path, target: Path, rel_path: str, abs_path: str
) -> None:
    """Read hook branch for files cgh cannot parse (pdf, images, office docs).
    Surfaces a saved summary if one exists, else nudges to record one after the
    inspection so the next read is free. Advisory and best-effort throughout: a
    missing DB or a bad row must never break the Read."""
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


def cmd_hook_precheck_read(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Read. When the file is indexed in cgh's FTS and the
    Read is a full read (no offset/limit), suggest file_outline / symbols_in_file
    first, both return structured summaries for a fraction of the tokens of
    a raw Read. The suggestion is delivered via
    hookSpecificOutput.additionalContext. Advisory: always exits 0.
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

    # Walk up to find the nearest .codegraph dir; that's the repo cgh
    # considers root for this file. Stops at filesystem root.
    repo_root: Path | None = None
    for parent in [target.parent, *target.parents]:
        if (parent / ".codegraph" / "fts.db").is_file():
            repo_root = parent
            break
    if repo_root is None:
        sys.exit(0)

    # cgh's FTS stores file_path as the absolute path on disk (see
    # codegraph/fts.py). Older indexes may have stored relpaths, so try
    # both for forward/backward compatibility.
    try:
        abs_path = str(target.resolve())
        rel_path = str(target.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        sys.exit(0)

    # Files cgh cannot parse into the graph (pdf, images, office docs) carry no
    # symbols, so the outline hint below never fires for them. Give them their
    # own treatment instead: surface a saved summary so an expensive re-read is
    # skipped, or nudge to save one after the inspection. This replaces the
    # symbol path for these files, it does not run in addition to it.
    try:
        from codegraph.cli.commands_artifact import ARTIFACT_EXTS
    except Exception:
        ARTIFACT_EXTS = frozenset()
    if target.suffix.lower() in ARTIFACT_EXTS:
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
    if symbol_count < _MIN_SYMBOLS_FOR_OUTLINE_HINT:
        sys.exit(0)

    is_markdown = target.suffix.lower() in (".md", ".markdown")
    if is_markdown:
        hint = (
            f"  - doc_outline('{rel_path}')   heading tree, cheap structural summary\n"
            f"  - search_docs('<keyword>')    BM25 search across all indexed Markdown\n"
        )
    else:
        hint = (
            f"  - subgraph('{rel_path}')      file + neighbors (imports, callers, classes)\n"
            f"  - imports_of('{rel_path}')    what this file pulls in\n"
            f"  - search_symbols('<name>')    if you're after a specific definition\n"
        )

    _emit_nudge(
        f"[cgh hook] '{rel_path}' is indexed by cgh ({symbol_count} symbols). "
        "Before a full Read, consider:\n"
        f"{hint}"
        "Then Read with offset/limit on the range you actually need. "
        "If you really want the whole file, ignore this and proceed."
    )
