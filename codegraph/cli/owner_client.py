# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared HTTP client the CLI uses to run one MCP tool on a live
#              per-repo owner. A running owner keeps the graph DB open for
#              writing for its whole lifetime, which blocks a read-only open
#              from any other process, so CLI graph queries ask the owner
#              first and only open the DB themselves when no owner answers.
#              Never starts an owner. Every call has a bounded timeout.

from __future__ import annotations

import http.client
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

# Default budget for one owner call. Graph queries answer in milliseconds;
# the ceiling only exists so a wedged owner cannot hang the CLI forever.
_DEFAULT_TIMEOUT = 30.0

OwnerStatus = Literal["ok", "absent", "timeout", "unknown_tool", "error"]

# How an MCP server says it has no tool by that name. FastMCP answers
# ``Unknown tool: 'name'`` as an isError result; the low-level MCP SDK says
# ``Unknown tool: name``; some versions answer with a JSON-RPC error instead.
_UNKNOWN_TOOL_RE = re.compile(r"^\s*unknown tool\b", re.IGNORECASE)


@dataclass
class OwnerReply:
    """Outcome of one owner call.

    ``status`` is "ok" (``data`` holds the tool's parsed JSON, ``text`` its
    raw text), "absent" (no live owner, nothing was sent), "timeout" (the
    owner accepted the call but did not answer in time), "unknown_tool"
    (the owner is alive but has no such tool, typically an owner started by
    an older cgh) or "error" (HTTP, auth, transport or tool error, detail in
    ``error``).
    """

    status: OwnerStatus
    data: Any = None
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def owner_timeout() -> float:
    """Seconds to wait for one owner call; ``CGH_OWNER_TIMEOUT`` overrides."""
    raw = os.environ.get("CGH_OWNER_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT
    return value if value > 0 else _DEFAULT_TIMEOUT


def live_owner_port(root: str) -> int | None:
    """The port of this repo's owner when it is alive and listening, else None."""
    from codegraph.state.ipc import is_owner_alive, read_owner_port

    try:
        if not is_owner_alive(root):
            return None
        return read_owner_port(root)
    except Exception:
        return None


def call_owner_tool(
    root: str,
    tool: str,
    arguments: dict | None = None,
    *,
    timeout: float | None = None,
    port: int | None = None,
) -> OwnerReply:
    """POST one ``tools/call`` to the repo's owner and parse the result.

    With ``port`` None the owner is looked up first and "absent" is returned
    without any network call when none is alive. The bearer token comes from
    ``.codegraph/auth.key``.
    """
    if port is None:
        port = live_owner_port(root)
        if not port:
            return OwnerReply("absent")
    if timeout is None:
        timeout = owner_timeout()

    from codegraph.state.auth import ensure_auth_key

    try:
        token = ensure_auth_key(root)
    except Exception as exc:
        return OwnerReply("error", error=f"auth key unavailable: {exc}")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
    )
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(
            "POST",
            "/mcp",
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
    except TimeoutError:
        return OwnerReply("timeout", error=f"no answer within {timeout:g}s")
    except OSError as exc:
        return OwnerReply("error", error=f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()
    if resp.status != 200:
        return OwnerReply("error", error=f"HTTP {resp.status}")
    return _parse_tool_result(raw)


def _parse_tool_result(raw: str) -> OwnerReply:
    """Extract the tool's text payload from a JSON or SSE JSON-RPC response."""
    envelope = None
    if raw.lstrip().startswith("{"):
        try:
            envelope = json.loads(raw)
        except ValueError:
            envelope = None
    else:
        # SSE: the last `data:` line carries the JSON-RPC envelope.
        for line in raw.splitlines():
            if line.startswith("data: "):
                try:
                    envelope = json.loads(line[6:])
                except ValueError:
                    continue
    if not isinstance(envelope, dict):
        return OwnerReply("error", error="malformed owner response")
    if envelope.get("error"):
        err = envelope["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        status: OwnerStatus = "unknown_tool" if _is_unknown_tool(msg) else "error"
        return OwnerReply(status, error=str(msg))
    result = envelope.get("result") or {}
    content = result.get("content") or []
    text = next(
        (c.get("text") for c in content if c.get("type") == "text" and c.get("text")),
        None,
    )
    if result.get("isError"):
        status = "unknown_tool" if _is_unknown_tool(text) else "error"
        return OwnerReply(status, error=text or "tool error")
    if text is None:
        return OwnerReply("error", error="empty tool result")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return OwnerReply("ok", data=data, text=text)


def _is_unknown_tool(message: object) -> bool:
    return isinstance(message, str) and bool(_UNKNOWN_TOOL_RE.match(message))


def older_owner_hint(root: str, tool: str) -> str:
    """Why a current CLI cannot use a live owner that lacks ``tool``."""
    return (
        f"The cgh owner running for this repo has no `{tool}` tool: it was "
        "started by an older cgh and still holds the graph, so this command "
        f"cannot open it either. Run `cgh stop --root {root}` (the next agent "
        "call starts a current owner) and retry, or retry once the agent "
        "sessions using the old owner have ended."
    )


def stuck_owner_hint(root: str, reply: OwnerReply) -> str:
    """One-line explanation plus the commands that unstick a silent owner."""
    return (
        "The cgh owner for this repo holds the graph but could not serve "
        f"this query ({reply.error}). "
        f"Check it with `cgh doctor --owner --root {root}` or restart it with "
        f"`cgh stop --root {root}`. CGH_OWNER_TIMEOUT raises the wait."
    )


def note_route(command: str, route: str) -> None:
    """With CGH_DEBUG_ROUTE=1, say on stderr which path served a command."""
    if os.environ.get("CGH_DEBUG_ROUTE") == "1":
        print(f"[cgh] {command}: served by {route}", file=sys.stderr)


Route = Literal["owner", "older", "local"]


def route_owner_read(
    root: str,
    command: str,
    tool: str,
    arguments: dict | None,
    on_stuck: Callable[[str], None],
) -> tuple[Route, dict | None]:
    """Decide who serves a read-only CLI query, asking the live owner first.

    Returns ``("owner", data)`` when the owner answered with a JSON object,
    ``("older", None)`` when an owner is alive but has no ``tool`` (an older
    cgh: it still holds the graph lock, so the caller must not try a local
    graph open) and ``("local", None)`` when no owner answered, in which case
    the caller opens the graph itself. A timed-out owner holds the lock and
    cannot serve: ``on_stuck`` gets the remedy text, then this exits 1.
    Never starts an owner.
    """
    reply = call_owner_tool(root, tool, arguments)
    if reply.status == "timeout":
        on_stuck(stuck_owner_hint(root, reply))
        raise SystemExit(1)
    if reply.status == "unknown_tool":
        return "older", None
    if reply.ok and isinstance(reply.data, dict):
        note_route(command, "owner")
        return "owner", reply.data
    note_route(command, "local read-only open")
    return "local", None


__all__ = [
    "OwnerReply",
    "call_owner_tool",
    "live_owner_port",
    "note_route",
    "older_owner_hint",
    "owner_timeout",
    "route_owner_read",
    "stuck_owner_hint",
]
