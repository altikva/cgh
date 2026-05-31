# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: IPC glue for multi-client cgh serve.
#              One "owner" process runs FastMCP HTTP on a loopback port.
#              Every Claude Code session launches its own cgh serve which
#              acts as a stdio <-> HTTP proxy to the shared owner.

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

_PORT_FILE = "server.port"
_OWNER_PID_FILE = "owner.pid"
_WORKERS_DIR = "workers"


def port_file(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _PORT_FILE


def owner_pidfile(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _OWNER_PID_FILE


def workers_dir(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _WORKERS_DIR


def register_worker(repo_root: str | Path) -> Path:
    """
    Drop a worker lock file named after this process's PID.
    Called by every proxy on startup. The owner uses the worker count
    to decide when to self-terminate.
    """
    wd = workers_dir(repo_root)
    wd.mkdir(parents=True, exist_ok=True)
    lock = wd / f"{os.getpid()}"
    lock.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    return lock


def unregister_worker(repo_root: str | Path) -> None:
    """Remove this process's worker lock. No-op if already gone."""
    try:
        (workers_dir(repo_root) / f"{os.getpid()}").unlink(missing_ok=True)
    except Exception:
        pass


def live_workers(repo_root: str | Path) -> list[int]:
    """
    Return PIDs of currently-alive workers. Stale entries (dead PIDs)
    are pruned from disk as a side effect. The synthetic "keepalive"
    marker (used by `cgh serve --background`) counts as one synthetic
    worker (pid 0) so the owner stays up across Claude sessions.
    """
    wd = workers_dir(repo_root)
    if not wd.exists():
        return []
    alive: list[int] = []
    for entry in wd.iterdir():
        if not entry.is_file():
            continue
        if entry.name == "keepalive":
            alive.append(0)
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if is_pid_alive(pid):
            alive.append(pid)
        else:
            # Stale — drop it
            entry.unlink(missing_ok=True)
    return alive


def register_keepalive(repo_root: str | Path) -> Path:
    """Drop a non-pid worker marker that persists across cgh process exits."""
    wd = workers_dir(repo_root)
    wd.mkdir(parents=True, exist_ok=True)
    marker = wd / "keepalive"
    marker.write_text("background\n", encoding="utf-8")
    return marker


def unregister_keepalive(repo_root: str | Path) -> None:
    """Remove the keepalive marker (called by `cgh serve --stop`)."""
    try:
        (workers_dir(repo_root) / "keepalive").unlink(missing_ok=True)
    except Exception:
        pass


def read_owner_port(repo_root: str | Path) -> int | None:
    p = port_file(repo_root)
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def read_owner_pid(repo_root: str | Path) -> int | None:
    p = owner_pidfile(repo_root)
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
        return pid if pid > 0 else None
    except (ValueError, OSError):
        return None


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def is_owner_alive(repo_root: str | Path) -> bool:
    pid = read_owner_pid(repo_root)
    port = read_owner_port(repo_root)
    if pid is None or port is None:
        return False
    if not is_pid_alive(pid):
        return False
    # Probe the port — owner may be mid-shutdown
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rotate_owner_log(repo_root: str | Path) -> None:
    """
    Rotate .codegraph/owner.log if it exceeds `log_max_mb`. Keeps the
    last `log_backup_count` rotations as `owner.log.1` … `owner.log.N`.

    Called at owner spawn — owners restart often enough (stop/start, new
    sessions, --reindex) that this bounds disk use without needing an
    interceptor process between owner stdout and the log file.
    """
    from codegraph.core.config import load_config

    log_path = Path(repo_root) / ".codegraph" / "owner.log"
    if not log_path.exists():
        return

    cfg = load_config(repo_root)
    max_bytes = max(0, cfg.log_max_mb) * 1024 * 1024
    backup_count = max(0, cfg.log_backup_count)

    # max_mb=0 disables rotation entirely (truncate-only would lose data
    # without warning, so we just no-op).
    if max_bytes == 0:
        return

    try:
        size = log_path.stat().st_size
    except OSError:
        return
    if size < max_bytes:
        return

    # Roll: owner.log.{N-1} -> owner.log.{N}, ..., owner.log -> owner.log.1
    # Drop the oldest if it exists.
    if backup_count == 0:
        log_path.unlink(missing_ok=True)
        return

    oldest = log_path.with_suffix(f".log.{backup_count}")
    oldest.unlink(missing_ok=True)
    for i in range(backup_count - 1, 0, -1):
        src = log_path.with_suffix(f".log.{i}")
        dst = log_path.with_suffix(f".log.{i + 1}")
        if src.exists():
            try:
                src.rename(dst)
            except OSError:
                pass
    try:
        log_path.rename(log_path.with_suffix(".log.1"))
    except OSError:
        # If rename fails (e.g. file held open on Windows) we bail rather
        # than truncate — losing logs silently is worse than a big file.
        pass


def spawn_owner(repo_root: str | Path, watch: bool, reindex: bool) -> int | None:
    """
    Launch `cgh _serve_owner` as a detached background process.
    Blocks up to ~5s waiting for the port file to appear.
    Returns the port on success, None on timeout.
    """
    repo_root = Path(repo_root).resolve()
    (repo_root / ".codegraph").mkdir(parents=True, exist_ok=True)

    # Clear any stale state
    port_file(repo_root).unlink(missing_ok=True)

    # Rotate the log if it grew too large since the last owner exit.
    rotate_owner_log(repo_root)

    cmd = [sys.executable, "-m", "codegraph", "_serve_owner", "--root", str(repo_root)]
    if watch:
        cmd.append("--watch")
    if reindex:
        cmd.append("--reindex")

    # Detach: own session, stdio redirected to DEVNULL, owner logs go
    # to .codegraph/owner.log
    log_path = repo_root / ".codegraph" / "owner.log"
    logf = open(log_path, "ab", buffering=0)
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=logf,
        start_new_session=True,
        close_fds=True,
    )

    # Wait for the owner to publish its port. When --reindex is requested the
    # owner finishes the full scan before writing the port file, which can take
    # well over a minute on large repos. Use a generous timeout.
    timeout = 300.0 if reindex else 15.0
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_owner_alive(repo_root):
            return read_owner_port(repo_root)
        time.sleep(0.25)
    return None


def proxy_stdio_to_http(port: int, repo_root: str | Path | None = None) -> int:
    """
    Bridge the current process's stdin/stdout to the owner's HTTP MCP
    endpoint at http://127.0.0.1:<port>/mcp/. One JSON-RPC message per
    stdin line in, one response line out.

    Auth: sends `Authorization: Bearer <key>` from .codegraph/auth.key
    (the same key shipped with `cgh init`). Owner rejects anything else.

    Returns an exit code (0 on clean EOF).
    """
    import http.client

    from codegraph.state.auth import ensure_auth_key

    auth_token = ensure_auth_key(repo_root) if repo_root else os.environ.get("CODEGRAPH_AUTH_KEY", "")

    url_path = "/mcp"

    def _open() -> http.client.HTTPConnection:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.connect()
        return c

    session_id: str | None = None

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {auth_token}",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        body = line.encode("utf-8")

        # One request, retry once on stale connection
        response_body: bytes = b""
        for attempt in (1, 2):
            try:
                conn = _open()
                conn.request("POST", url_path, body=body, headers=headers)
                resp = conn.getresponse()
                # Capture session header if present
                new_sid = resp.getheader("Mcp-Session-Id") or resp.getheader("mcp-session-id")
                if new_sid:
                    session_id = new_sid
                response_body = resp.read()
                conn.close()
                break
            except (ConnectionError, OSError) as exc:
                if attempt == 2:
                    err = {
                        "jsonrpc": "2.0",
                        "id": json.loads(line).get("id") if line else None,
                        "error": {"code": -32000, "message": f"proxy: {exc}"},
                    }
                    sys.stdout.write(json.dumps(err) + "\n")
                    sys.stdout.flush()
                time.sleep(0.1)

        if not response_body:
            continue

        # FastMCP http/json_response returns a plain JSON body per request
        try:
            text = response_body.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            # Strip SSE framing if present ("data: {...}\n\n")
            if text.startswith("data:"):
                lines = [ln[len("data:") :].strip() for ln in text.splitlines() if ln.startswith("data:")]
                text = "\n".join(ln for ln in lines if ln)
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": f"proxy decode: {exc}"},
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()

    return 0
