# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The stdio<->HTTP proxy self-heals when the owner it attached
#              to has died. A refused connection triggers a recovery
#              (attach to a respawned owner, or spawn one) and the request
#              retries against the new port instead of failing forever with
#              a dead-port error. Covers _recover_owner's three outcomes and
#              the end-to-end retry that turns Errno 61 into a live reply.

from __future__ import annotations

import io
import json

import pytest

from codegraph.state import ipc


def test_recover_owner_attaches_to_live_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: True)
    monkeypatch.setattr(ipc, "read_owner_port", lambda root: 4321)
    spawned = []
    monkeypatch.setattr(ipc, "spawn_owner", lambda *a, **k: spawned.append(1))
    assert ipc._recover_owner(tmp_path, watch=True) == 4321
    assert spawned == []  # never spawns when one is already alive


def test_recover_owner_spawns_when_dead(monkeypatch, tmp_path):
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: False)
    seen = {}

    def _spawn(root, watch, reindex):
        seen["watch"], seen["reindex"] = watch, reindex
        return 5555

    monkeypatch.setattr(ipc, "spawn_owner", _spawn)
    assert ipc._recover_owner(tmp_path, watch=True) == 5555
    assert seen == {"watch": True, "reindex": False}  # recovery never reindexes


def test_recover_owner_none_without_repo_root():
    assert ipc._recover_owner(None, watch=False) is None


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def getheader(self, name):
        return None

    def read(self):
        return self._body


class _DeadConn:
    """Simulates the old owner port: connect() is refused."""

    def connect(self):
        raise ConnectionRefusedError(61, "Connection refused")

    def request(self, *a, **k):  # pragma: no cover - never reached
        raise AssertionError("request on a dead connection")

    def close(self):
        pass


class _LiveConn:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def connect(self):
        pass

    def request(self, *a, **k):
        pass

    def getresponse(self):
        return _Resp(self._body)

    def close(self):
        pass


def test_proxy_retries_against_recovered_owner(monkeypatch, tmp_path):
    """A request that meets a dead owner port must recover and succeed,
    not emit a proxy error."""
    import http.client

    reply = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

    # The first connection (dead port 1111) is refused; after recovery the
    # port flips to 2222 and the connection succeeds.
    made: list[int] = []

    def _fake_conn(host, port, timeout=0):
        made.append(port)
        return _DeadConn() if port == 1111 else _LiveConn(reply)

    monkeypatch.setattr(http.client, "HTTPConnection", _fake_conn)
    monkeypatch.setattr(ipc, "_recover_owner", lambda root, watch: 2222)
    monkeypatch.setattr(
        "codegraph.state.auth.ensure_auth_key", lambda root: "k", raising=False
    )

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1}) + "\n")
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    rc = ipc.proxy_stdio_to_http(1111, repo_root=tmp_path, watch=True)
    assert rc == 0
    written = out.getvalue()
    assert '"result"' in written and "proxy:" not in written
    assert 1111 in made and 2222 in made  # tried dead port, then recovered one


def test_proxy_gives_up_cleanly_when_recovery_fails(monkeypatch, tmp_path):
    """If no owner can be recovered, the request fails with a proxy error
    rather than hanging or crashing."""
    import http.client

    monkeypatch.setattr(http.client, "HTTPConnection", lambda *a, **k: _DeadConn())
    monkeypatch.setattr(ipc, "_recover_owner", lambda root, watch: None)
    monkeypatch.setattr(
        "codegraph.state.auth.ensure_auth_key", lambda root: "k", raising=False
    )
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7}) + "\n")
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    rc = ipc.proxy_stdio_to_http(1111, repo_root=tmp_path, watch=True)
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["error"]["code"] == -32000 and payload["id"] == 7


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
