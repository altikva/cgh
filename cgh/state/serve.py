# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-18
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Lean stdio<->HTTP proxy entry for `cgh serve`.
#              Kept out of the cgh.server package on purpose: that package
#              imports FastMCP and every tool module at load (~1s), which the
#              proxy never uses. A proxy is a stateless bridge; it only needs
#              the IPC primitives here, so every `cgh serve` boots in ~0.15s
#              instead of paying for the owner's import graph.

from __future__ import annotations

import logging
import os
from pathlib import Path

from cgh.core.log import configure_background_logging
from cgh.core.timing import emit as _emit_timing
from cgh.core.utils import resolve_signals

_log = logging.getLogger(__name__)


def proxy_main(
    root: str | os.PathLike[str] | None = None,
    watch: bool = False,
    reindex: bool = False,
) -> int:
    """Run this process as a stdio<->HTTP bridge to the shared owner.

    Every `cgh serve` invocation acts as a bridge to a single shared
    "owner" backend. The first caller lazily spawns the owner; later
    callers reuse it. The owner holds the graph DB write lock; proxies are
    stateless bridges with no DB access.

    Returns the exit code the caller should exit with.
    """
    # First phase boundary: the gap from the caller's spawn of this process
    # to here is the Python interpreter boot plus the proxy's own import
    # cost, the part a native bridge would remove.
    _emit_timing("proxy_enter")

    resolved = Path(root if root is not None else os.getcwd()).resolve()

    from cgh.state.ipc import (
        installed_cgh_version,
        is_owner_alive,
        owner_version_current,
        proxy_stdio_to_http,
        read_owner_port,
        read_owner_version,
        register_worker,
        spawn_owner,
        stop_owner,
        unregister_worker,
    )

    # Register this proxy as a worker BEFORE spawning the owner, so the
    # owner's shutdown logic always sees at least one live worker.
    register_worker(resolved)

    # Release the worker slot on any exit (normal, SIGTERM, SIGHUP).
    import atexit as _atexit
    import signal as _signal

    _atexit.register(unregister_worker, resolved)

    def _graceful(signum, _frame):
        unregister_worker(resolved)
        _signal.signal(signum, _signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for _sig in resolve_signals("SIGTERM", "SIGHUP", "SIGINT"):
        try:
            _signal.signal(_sig, _graceful)
        except (ValueError, OSError):
            pass

    configure_background_logging()

    # Second boundary: the bridge setup (ipc import, worker registration,
    # logging) is done; owner discovery begins. Everything up to here is
    # proxy overhead a native bridge removes.
    _emit_timing("owner_discovery")

    # Start (or reuse) the shared owner. A live owner is reused only when it
    # is serving the installed version: one left running across a package
    # upgrade holds stale code that can fail on a lazy import, so restart it
    # rather than bridge to it.
    if is_owner_alive(resolved) and owner_version_current(resolved):
        port = read_owner_port(resolved)
        _log.info("attaching to existing owner on port %s", port)
    else:
        if is_owner_alive(resolved):
            _log.warning(
                "owner is running cgh %s but %s is installed; restarting it "
                "so calls run current code",
                read_owner_version(resolved),
                installed_cgh_version(),
            )
            stop_owner(resolved)
        else:
            _log.info("no owner running, launching one")
        port = spawn_owner(resolved, watch=watch, reindex=reindex)
        if port is None:
            _log.error("failed to start owner (see .cgh/owner.log)")
            unregister_worker(resolved)
            return 1
        _log.info("owner up on port %s", port)

    if port is None:
        # A live owner answered but published no port (crash mid-write, or a
        # torn port file). Fail closed rather than bridge to nothing.
        _log.error("owner is up but published no port")
        unregister_worker(resolved)
        return 1

    # Third boundary: the owner is up (spawned cold, or discovered warm) and
    # the proxy is about to enter its stdin read loop. owner_discovery ->
    # owner_ready is the engine startup; it is zero on the warm path.
    _emit_timing("owner_ready")

    # Act as the stdio <-> HTTP bridge for this session. Pass watch so a
    # mid-session owner death can be self-healed with the same posture the
    # session started with.
    _emit_timing("bridge_ready")
    return proxy_stdio_to_http(port, repo_root=resolved, watch=watch)
