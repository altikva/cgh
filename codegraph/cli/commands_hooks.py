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


def cmd_hook_precheck_grep(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Grep. Reads the hook payload from stdin, and when
    the pattern looks like a bare identifier prints a suggestion to stderr
    pointing at cgh's symbol-search MCP tools. Always exits 0, advisory,
    never blocking, so Claude can still run Grep when it really wants to.
    """
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not _IDENTIFIER_RE.match(pattern):
        sys.exit(0)

    print(
        f"[cgh hook] Grep pattern '{pattern}' looks like a bare identifier. "
        "Prefer cgh MCP tools for symbol search (zero-token server-side execution):\n"
        f"  - symbol_lookup('{pattern}')   exact definition (functions, classes, sections)\n"
        f"  - search_symbols('{pattern}')  fuzzy across the graph\n"
        f"  - find_callers('{pattern}')    incoming CALLS edges (functions only)\n"
        "Override: re-run Grep with a regex containing a metachar (e.g. '\\\\b' or '|') "
        "to confirm a raw text search is what you want.",
        file=sys.stderr,
    )
    sys.exit(0)


def cmd_hook_precheck_read(args: argparse.Namespace) -> None:
    """
    PreToolUse hook for Read. When the file is indexed in cgh's FTS and the
    Read is a full read (no offset/limit), suggest file_outline / symbols_in_file
    first, both return structured summaries for a fraction of the tokens of
    a raw Read. Advisory: always exits 0.
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

    print(
        f"[cgh hook] '{rel_path}' is indexed by cgh ({symbol_count} symbols). "
        "Before a full Read, consider:\n"
        f"{hint}"
        "Then Read with offset/limit on the range you actually need. "
        "If you really want the whole file, ignore this and proceed.",
        file=sys.stderr,
    )
    sys.exit(0)
