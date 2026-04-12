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
    """
    Proxy entrypoint: every `cgh serve` invocation acts as a stdio<->HTTP
    bridge to a single shared "owner" backend. The first caller lazily
    spawns the owner; subsequent callers reuse it.

    This is what Claude Code launches per session. The owner holds the
    Kuzu write lock; proxies are stateless bridges with no DB access.
    """
    global _root

    ap = argparse.ArgumentParser(description="codegraph MCP server (proxy mode)")
    ap.add_argument("--root", default=os.getcwd(), help="Repo root (default: CWD)")
    ap.add_argument("--watch", action="store_true", help="Request file watcher in the owner")
    ap.add_argument("--reindex", action="store_true", help="Request a full re-index in the owner")
    args = ap.parse_args()

    _root = Path(args.root).resolve()

    from codegraph.ipc import (
        is_owner_alive,
        proxy_stdio_to_http,
        read_owner_port,
        spawn_owner,
    )

    # Start (or reuse) the shared owner
    if is_owner_alive(_root):
        port = read_owner_port(_root)
        print(f"[codegraph] attaching to existing owner on port {port}", file=sys.stderr)
    else:
        print("[codegraph] no owner running — launching one", file=sys.stderr)
        port = spawn_owner(_root, watch=args.watch, reindex=args.reindex)
        if port is None:
            print(
                "[codegraph] failed to start owner (see .codegraph/owner.log)",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[codegraph] owner up on port {port}", file=sys.stderr)

    # Act as the stdio <-> HTTP bridge for this Claude session
    exit_code = proxy_stdio_to_http(port, repo_root=_root)
    sys.exit(exit_code)


def owner_main(root: str | None = None, watch: bool = False, reindex: bool = False) -> None:
    """
    Backend entrypoint — runs FastMCP over HTTP on a loopback port.
    Spawned by the proxy via `python -m codegraph _serve_owner`. Claude
    Code never launches this directly.
    """
    global _root

    _root = Path(root or os.getcwd()).resolve()

    # Single-writer guard on the owner itself
    from codegraph.pidfile import acquire as _pidfile_acquire

    acquired, other_pid = _pidfile_acquire(_root)
    if not acquired:
        print(
            f"[codegraph] another owner is running (pid {other_pid}); exiting.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load / ensure the auth key for HTTP bridge security
    from codegraph.auth import ensure_auth_key

    auth_key = ensure_auth_key(_root)

    # Reindex + watcher (if requested)
    if reindex:
        from codegraph.indexer import index_repo

        print(f"[codegraph owner] indexing {_root} …", file=sys.stderr, flush=True)
        try:
            stats = index_repo(_root, verbose=False)
            print(f"[codegraph owner] done: {stats}", file=sys.stderr, flush=True)
        except RuntimeError as exc:
            print(f"[codegraph owner] reindex skipped: {exc}", file=sys.stderr, flush=True)

    if watch:
        from codegraph.watcher import start_watcher

        try:
            start_watcher(_root)
        except Exception as exc:
            print(f"[codegraph owner] watcher disabled: {exc}", file=sys.stderr, flush=True)

    # Pick a free port + publish port file + owner pid
    from codegraph.ipc import free_port, owner_pidfile, port_file

    port = free_port()
    port_file(_root).write_text(str(port) + "\n")
    owner_pidfile(_root).write_text(str(os.getpid()) + "\n")

    # Cleanup on exit
    import atexit as _atexit

    def _cleanup():
        try:
            port_file(_root).unlink(missing_ok=True)
            owner_pidfile(_root).unlink(missing_ok=True)
        except Exception:
            pass

    _atexit.register(_cleanup)

    # Build auth middleware — rejects any request without the bearer token
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp

    class _BearerAuth(BaseHTTPMiddleware):
        def __init__(self, app: ASGIApp, token: str) -> None:
            super().__init__(app)
            self._token = token

        async def dispatch(self, request, call_next):
            # Accept any path on 127.0.0.1 with correct bearer
            header = request.headers.get("authorization", "")
            expected = f"Bearer {self._token}"
            if header != expected:
                return JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                )
            return await call_next(request)

    print(
        f"[codegraph owner] listening on 127.0.0.1:{port} (auth: bearer)",
        file=sys.stderr,
        flush=True,
    )

    # Starlette middleware expects a different wiring than ASGIMiddleware
    # FastMCP's run_http_async uses middleware=[ASGIMiddleware(...)]; since
    # BaseHTTPMiddleware is an ASGIApp factory, we can wrap with a Middleware
    # class from starlette.
    from starlette.middleware import Middleware

    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=port,
        show_banner=False,
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(_BearerAuth, token=auth_key)],
    )


if __name__ == "__main__":
    main()
