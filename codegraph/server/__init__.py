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


def _resolve_signals(*names: str) -> list:
    """Return the signals from ``names`` that exist on this platform.

    SIGHUP and other POSIX signals are absent on Windows. Referencing
    ``signal.SIGHUP`` directly there raises AttributeError, which would
    crash `cgh serve` on startup, so resolve by name and skip what is
    missing.
    """
    import signal as _signal

    found = []
    for name in names:
        sig = getattr(_signal, name, None)
        if sig is not None:
            found.append(sig)
    return found


_conn = None
_root: Path | None = None
_fts_conn = None


def _logged_tool(fn):
    """Decorator that logs every MCP tool call to call_log.db."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from codegraph.state.call_log import log_call

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
        from codegraph.core.db import get_connection

        _conn = get_connection(_root)
    return _conn


def _get_fts():
    global _fts_conn
    if _fts_conn is None:
        from codegraph.core.fts import get_fts_conn

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
        "Local code graph + Claude Code memory/plan index.\n\n"
        "CALL THESE TOOLS BEFORE READING FILES, they return exact file paths\n"
        "and line numbers so you only read the specific lines you need.\n\n"
        "Tool execution is SERVER-SIDE and costs zero model tokens. Only the\n"
        "JSON response counts (capped + truncated). When in doubt: call the\n"
        "tool. It is almost always cheaper than Read/Grep over full files.\n\n"
        "Workflow matrix:\n"
        "  • Task kickoff / broad question ('how does X work', 'where to add Y'):\n"
        "       1. context_for_task(task, session_id?), merges code + memory + plans\n"
        "       2. architecture_overview() or domain_map(keyword) for structure\n"
        "       3. endpoints(path_pattern) for API questions\n"
        "  • Known user preference territory (commit style, naming, workflow):\n"
        "       1. memory_search(query, kind='feedback') BEFORE asking the user\n"
        "  • User hints at a past plan ('the refactor we planned'):\n"
        "       1. plan_search(query)\n"
        "  • Problem that might have been solved before (gotchas, patterns):\n"
        "       1. knowledge_search(query), persisted learnings across sessions\n"
        "  • You learn something worth remembering:\n"
        "       1. knowledge_record(title, body, kind, tags)\n"
        "  • Context ~80% full (long session, many results):\n"
        "       1. knowledge_record(...) for EVERY non-trivial insight\n"
        "       2. compact_session(session_id, title, digest)\n"
        "       These survive compaction, raw conversation does NOT.\n"
        "  • After compaction / session resume / new session:\n"
        "       1. knowledge_list(limit=20), reload recent learnings\n"
        "       2. knowledge_search(query), targeted reload\n"
        "       3. memory_search(query), user preferences + feedback\n"
        "       4. plan_search(query), active plans\n"
        "       CRITICAL: without reload you restart from zero.\n"
        "  • Symbol lookup ('where is Foo', 'what calls Bar'):\n"
        "       1. symbol_lookup / find_callers / find_callees\n"
        "       2. search_symbols for fuzzy, fts_search for docstrings\n"
        "  • Text/regex pattern search ('find every occurrence of X'):\n"
        "       1. pattern_search(pattern, glob?), INSTEAD of Grep\n"
        "          Returns {file, line, text}. Then Read only those lines.\n"
        "  • After git pull / checkout / rebase:\n"
        "       1. scan_status, then incremental_reindex if stale\n"
        "  • Adding an external dir: add_directory(path)\n"
        "\n"
        "FEDERATION (parent + subrepos):\n"
        "  When the project declares `subrepos = […]` in config.toml, every\n"
        "  read-side query tool (symbol_lookup, search_symbols, find_callers,\n"
        "  find_callees, imports_of, subgraph, fts_search, pattern_search,\n"
        "  search_docs, doc_outline, doc_refs, architecture_overview,\n"
        "  domain_map, endpoints, find_dead_code) automatically fans out to\n"
        "  each subrepo's read-only DB and aggregates results.\n"
        "  • Each result has a `scope` field: 'parent' or '<subrepo-name>'.\n"
        "    Use it to disambiguate when the same symbol exists in multiple\n"
        "    repos, and to route file Read calls to the right tree.\n"
        "  • Cross-repo edges are NOT inferred, find_callers in subrepo A\n"
        "    won't surface callers from subrepo B; subgraph imports stop at\n"
        "    repo boundaries. Treat scopes as independent islands.\n"
        "  • find_dead_code is per-scope: a 'dead' symbol may actually be\n"
        "    called from another repo. Don't delete blindly across scopes.\n"
        "  • If a child DB is locked or unavailable, the response includes\n"
        "    `partial: true` and `warnings: [{scope, error}]`. Results are\n"
        "    still useful, just incomplete for that scope. Re-query later.\n"
        "  • Knowledge / memory / plans are NEVER federated, each project\n"
        "    keeps its own. knowledge_search reads only the parent's store.\n"
        "Only use Read for the exact line range returned above.\n"
    ),
)

# Register tools from sub-modules (must be after mcp = FastMCP)
from codegraph.server.tools_arch import register as _register_arch  # noqa: E402
from codegraph.server.tools_docs import register as _register_docs  # noqa: E402
from codegraph.server.tools_index import register as _register_index  # noqa: E402
from codegraph.server.tools_insight import register as _register_insight  # noqa: E402
from codegraph.server.tools_knowledge import register as _register_knowledge  # noqa: E402
from codegraph.server.tools_memory import register as _register_memory  # noqa: E402
from codegraph.server.tools_meta import register as _register_meta  # noqa: E402
from codegraph.server.tools_plans import register as _register_plans  # noqa: E402
from codegraph.server.tools_query import register as _register_query  # noqa: E402
from codegraph.server.tools_viz import register as _register_viz  # noqa: E402

_register_arch(mcp)  # architecture_overview, domain_map, endpoints, use FIRST
_register_query(mcp)
_register_insight(mcp)  # file_summary, impact_of, path_between, import_cycles
_register_docs(mcp)
_register_index(mcp)
_register_viz(mcp)
_register_meta(mcp)
_register_memory(mcp)
_register_plans(mcp)
_register_knowledge(mcp)


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
    ap.add_argument(
        "--watch", action="store_true", help="Request file watcher in the owner"
    )
    ap.add_argument(
        "--reindex", action="store_true", help="Request a full re-index in the owner"
    )
    args = ap.parse_args()

    _root = Path(args.root).resolve()

    from codegraph.state.ipc import (
        is_owner_alive,
        proxy_stdio_to_http,
        read_owner_port,
        register_worker,
        spawn_owner,
        unregister_worker,
    )

    # Register this proxy as a worker BEFORE spawning the owner, so the
    # owner's shutdown logic always sees at least one live worker.
    register_worker(_root)

    # Release the worker slot on any exit (normal, SIGTERM, SIGHUP).
    import atexit as _atexit
    import signal as _signal

    _atexit.register(unregister_worker, _root)

    def _graceful(signum, _frame):
        unregister_worker(_root)
        _signal.signal(signum, _signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for _sig in _resolve_signals("SIGTERM", "SIGHUP", "SIGINT"):
        try:
            _signal.signal(_sig, _graceful)
        except (ValueError, OSError):
            pass

    # Start (or reuse) the shared owner
    if is_owner_alive(_root):
        port = read_owner_port(_root)
        print(
            f"[codegraph] attaching to existing owner on port {port}", file=sys.stderr
        )
    else:
        print("[codegraph] no owner running, launching one", file=sys.stderr)
        port = spawn_owner(_root, watch=args.watch, reindex=args.reindex)
        if port is None:
            print(
                "[codegraph] failed to start owner (see .codegraph/owner.log)",
                file=sys.stderr,
            )
            unregister_worker(_root)
            sys.exit(1)
        print(f"[codegraph] owner up on port {port}", file=sys.stderr)

    # Act as the stdio <-> HTTP bridge for this Claude session
    exit_code = proxy_stdio_to_http(port, repo_root=_root)
    sys.exit(exit_code)


def owner_main(
    root: str | None = None, watch: bool = False, reindex: bool = False
) -> None:
    """
    Backend entrypoint, runs FastMCP over HTTP on a loopback port.
    Spawned by the proxy via `python -m codegraph _serve_owner`. Claude
    Code never launches this directly.
    """
    global _root

    _root = Path(root or os.getcwd()).resolve()

    # Single-writer guard on the owner itself
    from codegraph.state.pidfile import acquire as _pidfile_acquire

    acquired, other_pid = _pidfile_acquire(_root)
    if not acquired:
        print(
            f"[codegraph] another owner is running (pid {other_pid}); exiting.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load / ensure the auth key for HTTP bridge security
    from codegraph.state.auth import ensure_auth_key

    auth_key = ensure_auth_key(_root)

    # Reindex + watcher (if requested)
    if reindex:
        from codegraph.indexer import index_repo

        print(f"[codegraph owner] indexing {_root} …", file=sys.stderr, flush=True)
        try:
            stats = index_repo(_root, verbose=False)
            print(f"[codegraph owner] done: {stats}", file=sys.stderr, flush=True)
        except RuntimeError as exc:
            print(
                f"[codegraph owner] reindex skipped: {exc}", file=sys.stderr, flush=True
            )

    if watch:
        from codegraph.state.watcher import start_watcher

        try:
            start_watcher(_root)
        except Exception as exc:
            print(
                f"[codegraph owner] watcher disabled: {exc}",
                file=sys.stderr,
                flush=True,
            )

    # Pick a free port + publish port file + owner pid
    from codegraph.state.ipc import free_port, owner_pidfile, port_file

    port = free_port()
    port_file(_root).write_text(str(port) + "\n", encoding="utf-8")
    owner_pidfile(_root).write_text(str(os.getpid()) + "\n", encoding="utf-8")

    # Cleanup on exit
    import atexit as _atexit

    def _cleanup():
        try:
            port_file(_root).unlink(missing_ok=True)
            owner_pidfile(_root).unlink(missing_ok=True)
            # Release the single-writer pidfile the owner acquired.
            from codegraph.state.pidfile import release as _pidfile_release

            _pidfile_release(_root)
            # Clear the workers dir (entries + dir itself if empty).
            wd = _root / ".codegraph" / "workers"
            if wd.exists():
                for f in wd.iterdir():
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
                try:
                    wd.rmdir()
                except OSError:
                    pass
        except Exception:
            pass

    _atexit.register(_cleanup)

    # Build auth middleware, rejects any request without the bearer token
    import hmac

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
            # Constant-time compare so the loopback port gives no timing oracle
            # on the token (this is the system's only auth check).
            if not hmac.compare_digest(header, expected):
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

    # Background thread: once at least one worker has registered, shut
    # the owner down as soon as all workers exit. Grace windows are
    # intentionally conservative to tolerate the brief gap while a new
    # Claude session is starting its proxy.
    import signal as _sig
    import threading as _th
    import time as _time

    from codegraph.state.ipc import live_workers

    _seen_worker = False
    _idle_since: float | None = None

    def _shutdown(reason: str) -> None:
        print(f"[codegraph owner] {reason}, shutting down", file=sys.stderr, flush=True)
        # Run cleanup explicitly, SIGTERM + os._exit would skip atexit.
        try:
            _cleanup()
        except Exception:
            pass
        # Release Kuzu lock so a subsequent owner can start immediately.
        try:
            from codegraph.core.db import reset_connection

            reset_connection()
        except Exception:
            pass
        # Hard exit, uvicorn has its own signal handlers and blocks
        # cooperative shutdown from threads; os._exit gets us out reliably.
        os._exit(0)

    def _watch_workers() -> None:
        nonlocal _seen_worker, _idle_since
        grace_before_first = 30.0  # wait this long for the first worker
        grace_while_idle = 5.0  # then exit after workers leave

        started_at = _time.time()
        while True:
            _time.sleep(1.0)
            workers = live_workers(_root)

            if workers:
                _seen_worker = True
                _idle_since = None
                continue

            if not _seen_worker:
                if _time.time() - started_at > grace_before_first:
                    _shutdown(f"no workers connected within {grace_before_first:.0f}s")
                    return
                continue

            if _idle_since is None:
                _idle_since = _time.time()
            elif _time.time() - _idle_since > grace_while_idle:
                _shutdown("last worker exited")
                return

    _th.Thread(target=_watch_workers, daemon=True).start()

    # SIGTERM / SIGINT from outside (kill, Ctrl-C) also do a clean shutdown.
    def _signal_shutdown(signum, _frame):
        _shutdown(f"received {_sig.Signals(signum).name}")

    for s in _resolve_signals("SIGTERM", "SIGINT"):
        try:
            _sig.signal(s, _signal_shutdown)
        except (ValueError, OSError):
            pass

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
