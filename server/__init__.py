# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: fastmcp MCP server exposing code graph query tools to Claude Code.
#              Run with:  python -m codegraph.server --root /path/to/repo

from __future__ import annotations

import argparse
import functools
import os
import sys
import time as _time
from pathlib import Path

from fastmcp import FastMCP

from codegraph.core.utils import short_path

# ---------------------------------------------------------------------------
# Module-level globals
# ---------------------------------------------------------------------------

_conn = None
_root: Path | None = None
_fts_conn = None


def _logged_tool(fn):
    """Decorator that logs every MCP tool call to call_log.db."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from codegraph.call_log import log_call

        tool_name = fn.__name__
        t0 = _time.perf_counter()
        success = True
        error = None
        result = ""
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as exc:
            success = False
            error = str(exc)[:500]
            raise
        finally:
            latency = (_time.perf_counter() - t0) * 1000
            try:
                log_call(
                    tool=tool_name,
                    args=kwargs or {f"arg{i}": v for i, v in enumerate(args)},
                    latency_ms=round(latency, 2),
                    result_size=len(result) if isinstance(result, str) else 0,
                    success=success,
                    error=error,
                    repo_root=_root,
                )
            except Exception:
                pass  # never let logging break the tool

    return wrapper


def _get_conn():
    global _conn
    if _conn is None:
        from codegraph.db import get_connection

        _conn = get_connection(_root)
    return _conn


def _get_fts():
    global _fts_conn
    if _fts_conn is None:
        from codegraph.fts import get_fts_conn

        _fts_conn = get_fts_conn(_root)
    return _fts_conn


def _short_path(path: str) -> str:
    """Shorten a file path for display (uses _root module global)."""
    if _root:
        return short_path(path, str(_root))
    return path


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="codegraph",
    instructions=(
        "Local code graph index for this repository.  "
        "Use these tools BEFORE reading files — they return exact "
        "file paths and line numbers so you only need to read the "
        "specific lines you need, saving tokens."
    ),
)

# Register tools from sub-modules (must be after mcp = FastMCP)
from codegraph.server.tools_docs import register as _register_docs  # noqa: E402
from codegraph.server.tools_index import register as _register_index  # noqa: E402
from codegraph.server.tools_meta import register as _register_meta  # noqa: E402
from codegraph.server.tools_query import register as _register_query  # noqa: E402
from codegraph.server.tools_viz import register as _register_viz  # noqa: E402

_register_query(mcp)
_register_docs(mcp)
_register_index(mcp)
_register_viz(mcp)
_register_meta(mcp)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    global _root

    ap = argparse.ArgumentParser(description="codegraph MCP server")
    ap.add_argument("--root", default=os.getcwd(), help="Repo root (default: CWD)")
    ap.add_argument("--watch", action="store_true", help="Also start the file watcher in a background thread")
    ap.add_argument("--reindex", action="store_true", help="Run a full re-index before starting the server")
    args = ap.parse_args()

    _root = Path(args.root).resolve()

    # MCP stdio transport reserves stdout for JSON-RPC. Redirect all
    # human-readable output (reindex progress, watcher logs, FastMCP banner)
    # to stderr so it doesn't corrupt the protocol stream.
    sys.stdout.flush()
    _orig_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        if args.reindex:
            from codegraph.indexer import index_repo

            print(f"[codegraph] indexing {_root} …", flush=True)
            stats = index_repo(_root, verbose=True)
            print(f"[codegraph] done: {stats}", flush=True)

        if args.watch:
            from codegraph.watcher import start_watcher

            start_watcher(_root)
    finally:
        # Restore stdout for MCP JSON-RPC
        sys.stdout = _orig_stdout

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
