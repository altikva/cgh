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


def port_file(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _PORT_FILE


def owner_pidfile(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _OWNER_PID_FILE


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

    # Wait for the owner to publish its port
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if is_owner_alive(repo_root):
            return read_owner_port(repo_root)
        time.sleep(0.1)
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

    from codegraph.auth import ensure_auth_key

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
