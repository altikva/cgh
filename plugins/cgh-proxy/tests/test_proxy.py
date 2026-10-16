# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh proxy against a mocked gateway: forwarding (JSON and
#              SSE), what lands on disk (no credential, 0600, last N kept),
#              token usage and anatomy, PII counts without values, the
#              upstream and TLS checks, and the 502 when the gateway is down.

from __future__ import annotations

import json
import os
import stat

import pytest

pytest.importorskip("cgh_proxy")
httpx = pytest.importorskip("httpx")
from cgh_proxy import ProxyError
from cgh_proxy.app import ProxyConfig, build_app, check_upstream, tls_context
from cgh_proxy.capture import CaptureStore
from cgh_proxy.messages import anatomy, response_facts
from starlette.testclient import TestClient

UPSTREAM = "http://127.0.0.1:4000"
SECRET = "sk-litellm-not-for-disk"

REQUEST = {
    "model": "claude-sonnet",
    "max_tokens": 64,
    "system": [{"type": "text", "text": "You are terse."}],
    "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
    "messages": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "a.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x = 1"}]},
    ],
}  # fmt: skip

SSE = (
    'event: message_start\ndata: {"type":"message_start","message":{"model":'
    '"claude-sonnet","usage":{"input_tokens":12,"cache_read_input_tokens":40}}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hi"}}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":'
    '"end_turn"},"usage":{"output_tokens":3}}\n\n'
)


class Gateway:
    """A mocked LiteLLM: records what it got, answers JSON or SSE."""

    def __init__(self, stream: bool = False) -> None:
        self.stream = stream
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.stream:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=SSE
            )
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )


def _client(tmp_path, gateway, keep: int = 20, scan: bool = False) -> TestClient:
    cfg = ProxyConfig(upstream=UPSTREAM, capture_dir=tmp_path, keep=keep, scan=scan)
    return TestClient(build_app(cfg, transport=httpx.MockTransport(gateway)))


def _post(client: TestClient, body: dict = REQUEST):
    return client.post(
        "/v1/messages?beta=true",
        json=body,
        headers={"authorization": f"Bearer {SECRET}", "x-api-key": SECRET,
                 "anthropic-version": "2023-06-01"},
    )  # fmt: skip


def test_json_exchange_is_forwarded_and_recorded(tmp_path):
    gateway = Gateway()
    with _client(tmp_path, gateway) as client:
        resp = _post(client)
    assert resp.status_code == 200 and resp.json()["usage"]["output_tokens"] == 3

    sent = gateway.seen[0]
    assert str(sent.url) == f"{UPSTREAM}/v1/messages?beta=true"
    assert sent.headers["authorization"] == f"Bearer {SECRET}"  # passed through
    assert json.loads(sent.content) == REQUEST

    [row] = CaptureStore(tmp_path, keep=0).recent()
    assert row["status"] == 200 and row["request"]["messages"] == 3
    assert row["response"]["usage"] == {"input_tokens": 12, "output_tokens": 3}

    cap_file = next(tmp_path.glob("cap-*.json"))
    assert stat.S_IMODE(os.stat(cap_file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
    on_disk = cap_file.read_text() + (tmp_path / "journal.jsonl").read_text()
    assert SECRET not in on_disk  # credentials never stored
    cap = json.loads(cap_file.read_text())
    assert cap["request"]["body"] == REQUEST
    assert cap["request"]["headers"]["anthropic-version"] == "2023-06-01"


def test_stream_is_relayed_intact_and_usage_reassembled(tmp_path):
    with _client(tmp_path, Gateway(stream=True)) as client:
        resp = _post(client, {**REQUEST, "stream": True})
    assert resp.text == SSE
    assert resp.headers["content-type"].startswith("text/event-stream")
    [row] = CaptureStore(tmp_path, keep=0).recent()
    assert row["response"]["stop_reason"] == "end_turn"
    assert row["response"]["usage"] == {
        "input_tokens": 12, "cache_read_input_tokens": 40, "output_tokens": 3,
    }  # fmt: skip


def test_only_the_last_captures_are_kept(tmp_path):
    with _client(tmp_path, Gateway(), keep=2) as client:
        for _ in range(4):
            _post(client)
    assert len(list(tmp_path.glob("cap-*.json"))) == 2
    assert len(CaptureStore(tmp_path, keep=0).recent()) == 4  # journal keeps all


def test_keep_zero_stores_no_body(tmp_path):
    with _client(tmp_path, Gateway(), keep=0) as client:
        _post(client)
    assert not list(tmp_path.glob("cap-*.json"))
    assert len(CaptureStore(tmp_path, keep=0).recent()) == 1


def test_pii_counts_without_values(tmp_path):
    pytest.importorskip("cgh_pii")
    email = "jeanne.martin@example.org"
    body = {**REQUEST, "messages": [*REQUEST["messages"][:2], {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": f"contact: {email}"}]}]}  # fmt: skip
    with _client(tmp_path, Gateway(), scan=True) as client:
        _post(client, body)
    [row] = CaptureStore(tmp_path, keep=0).recent()
    assert row["pii"]["last_message"].get("pii.email") == 1
    assert email not in (tmp_path / "journal.jsonl").read_text()


def test_gateway_down_gives_an_anthropic_error(tmp_path):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with _client(tmp_path, down) as client:
        resp = _post(client)
    assert resp.status_code == 502
    assert resp.json()["type"] == "error"
    [row] = CaptureStore(tmp_path, keep=0).recent()
    assert row["status"] == 502 and row["error"] == "ConnectError"


def test_upstream_must_be_encrypted_unless_local():
    assert check_upstream("https://gw.example.com/") == "https://gw.example.com"
    assert check_upstream("http://localhost:4000") == "http://localhost:4000"
    with pytest.raises(ProxyError, match="https"):
        check_upstream("http://gw.example.com")
    with pytest.raises(ProxyError):
        check_upstream("ftp://gw.example.com")


def test_client_cert_needs_its_key(tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    cfg = ProxyConfig(upstream=UPSTREAM, capture_dir=tmp_path, client_cert=cert)
    with pytest.raises(ProxyError, match="key"):
        tls_context(cfg)


def test_anatomy_and_non_stream_facts():
    a = anatomy(REQUEST)
    assert a["tools"] == 1 and a["messages"] == 3 and a["last_role"] == "user"
    assert set(a["block_bytes"]) == {"text", "tool_use", "tool_result"}
    assert a["system_bytes"] > 0
    facts = response_facts(
        "application/json", '{"type":"error","error":{"type":"overloaded_error"}}'
    )
    assert facts["error"] == "overloaded_error"
