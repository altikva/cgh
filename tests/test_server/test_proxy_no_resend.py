# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The stdio proxy must deliver each tool call to the owner exactly
#              once. It used to read the response under a 60 s timeout and
#              treat the timeout like a dead owner, resending the same request:
#              a slow tool (codegen, a reindex) ran up to 3 times, and a
#              codegen_write overwrote its own first result. Runs against a real
#              local HTTP server that counts what it receives.

from __future__ import annotations

import http.client
import http.server
import io
import json
import threading
import time

import pytest

from codegraph.state import ipc


class _Owner(http.server.ThreadingHTTPServer):
    """A stand-in owner: counts requests, answers after `delay` seconds, or
    drops the connection without answering when `drop` is set."""

    def __init__(self, delay: float = 0.0, drop: bool = False) -> None:
        self.hits: list[float] = []
        self.delay, self.drop = delay, drop
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits.append(time.monotonic())
                time.sleep(owner.delay)
                if owner.drop:
                    self.close_connection = True
                    self.connection.close()
                    return
                body = b'{"jsonrpc":"2.0","id":1,"result":{"ran":true}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


def _call(monkeypatch, port: int) -> str:
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}) + "\n"
        ),
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr(ipc, "_recover_owner", lambda *a, **k: None)
    assert ipc.proxy_stdio_to_http(port) == 0
    return out.getvalue()


@pytest.fixture
def short_timeouts(monkeypatch):
    # Shrink "a tool slower than the proxy's timeout" into a fast test: every
    # connection the proxy opens gets a 0.5 s timeout. A proxy that keeps a
    # read timeout on the response then sees the 1.5 s tool call time out.
    real = http.client.HTTPConnection
    monkeypatch.setattr(
        http.client,
        "HTTPConnection",
        lambda host, port, timeout=None: real(host, port, timeout=0.5),
    )


def test_a_slow_tool_call_runs_once_and_returns_its_result(monkeypatch, short_timeouts):
    owner = _Owner(delay=1.5)
    try:
        out = _call(monkeypatch, owner.server_address[1])
        time.sleep(0.2)  # let any resent copy land before counting
    finally:
        owner.shutdown()

    assert len(owner.hits) == 1
    assert json.loads(out)["result"] == {"ran": True}


def test_a_request_dropped_after_delivery_is_not_resent(monkeypatch):
    owner = _Owner(drop=True)
    try:
        out = _call(monkeypatch, owner.server_address[1])
        time.sleep(0.2)
    finally:
        owner.shutdown()

    assert len(owner.hits) == 1
    error = json.loads(out)["error"]
    assert error["code"] == -32000 and "not resent" in error["message"]
