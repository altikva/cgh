# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The proxy itself, a Starlette app Claude Code reaches through
#              ANTHROPIC_BASE_URL. Every request goes to the LiteLLM
#              gateway unchanged, over TLS with an optional client
#              certificate (mTLS); the answer streams back chunk by chunk.
#              Once an exchange ends, it is recorded off the hot path:
#              anatomy, token usage, PII counts, and the raw capture.
#              ProxyConfig     upstream, capture dir, keep, TLS material
#              check_upstream  https only, plain http for loopback alone
#              tls_context     the SSL context, client cert included
#              build_app       the ASGI app (an httpx transport can be
#                              injected for tests)
#              serve           run it on 127.0.0.1

from __future__ import annotations

import ipaddress
import json
import logging
import ssl
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import ProxyError
from .capture import CaptureStore, kept_headers
from .messages import anatomy, response_facts, scan_outbound

log = logging.getLogger(__name__)

_HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authorization",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "accept-encoding",
}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    upstream: str
    capture_dir: Path
    keep: int = 20
    scan: bool = True
    client_cert: Path | None = None
    client_key: Path | None = None
    ca_cert: Path | None = None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_upstream(url: str) -> str:
    """The gateway URL without its trailing slash. The point of the proxy
    is an encrypted link, so plain http is refused unless the gateway is
    on this machine."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ProxyError(f"upstream must be an http(s) URL, got {url!r}")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise ProxyError(
            f"upstream {parts.hostname} is remote: use https, plain http would "
            "send the traffic in clear"
        )
    return url.strip().rstrip("/")


def tls_context(cfg: ProxyConfig) -> ssl.SSLContext:
    if bool(cfg.client_cert) != bool(cfg.client_key):
        raise ProxyError("a client certificate needs its key, and the other way round")
    for path in (cfg.client_cert, cfg.client_key, cfg.ca_cert):
        if path is not None and not Path(path).is_file():
            raise ProxyError(f"no such file: {path}")
    ctx = ssl.create_default_context(cafile=str(cfg.ca_cert) if cfg.ca_cert else None)
    if cfg.client_cert and cfg.client_key:
        ctx.load_cert_chain(str(cfg.client_cert), str(cfg.client_key))
    return ctx


def _record(
    store: CaptureStore,
    cfg: ProxyConfig,
    started: float,
    method: str,
    path: str,
    req_headers,
    req_body: bytes,
    status: int,
    resp_headers,
    resp_body: bytes,
    error: str = "",
) -> None:
    try:
        req_json = json.loads(req_body) if req_body else None
    except ValueError:
        req_json = None
    resp_text = resp_body.decode("utf-8", "replace")
    content_type = (resp_headers or {}).get("content-type", "")
    summary: dict = {
        "ts": round(started, 3),
        "method": method,
        "path": path,
        "status": status,
        "ms": int((time.time() - started) * 1000),
        "request_bytes": len(req_body),
        "response_bytes": len(resp_body),
    }
    if error:
        summary["error"] = error
    is_messages = isinstance(req_json, dict) and "messages" in req_json
    if is_messages:
        summary["request"] = anatomy(req_json)
        # count_tokens re-sends the same content as the next call: scanning
        # it too would count every finding twice.
        if cfg.scan and not path.endswith("count_tokens"):
            summary["pii"] = scan_outbound(req_json)
    if resp_body:
        summary["response"] = response_facts(content_type, resp_text)
    capture = {
        "request": {
            "headers": kept_headers(req_headers),
            "body": req_json
            if req_json is not None
            else req_body.decode("utf-8", "replace"),
        },
        "response": {
            "status": status,
            "headers": kept_headers(resp_headers or {}),
            "body": resp_text,
        },
    }
    try:
        store.save(store.new_id(started), summary, capture)
    except OSError:
        log.exception("could not record the exchange")


def build_app(cfg: ProxyConfig, transport=None):
    import anyio
    import httpx
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    upstream = check_upstream(cfg.upstream)
    store = CaptureStore(cfg.capture_dir, cfg.keep)
    client = httpx.AsyncClient(
        verify=tls_context(cfg),
        transport=transport,
        timeout=httpx.Timeout(600.0, connect=10.0),
    )

    async def forward(request):
        started = time.time()
        body = await request.body()
        path = "/" + request.path_params["path"]
        url = upstream + path + (f"?{request.url.query}" if request.url.query else "")
        headers = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in request.headers.raw
            if k.decode("latin-1").lower() not in _HOP_HEADERS
        ]
        headers.append(("accept-encoding", "identity"))
        upstream_req = client.build_request(
            request.method, url, headers=headers, content=body
        )

        def record(status: int, resp_headers, resp_body: bytes, error: str = ""):
            return anyio.to_thread.run_sync(
                lambda: _record(
                    store, cfg, started, request.method, path, request.headers,
                    body, status, resp_headers, resp_body, error,
                )
            )  # fmt: skip

        try:
            resp = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            log.warning("upstream unreachable: %s", type(exc).__name__)
            await record(502, {}, b"", type(exc).__name__)
            return JSONResponse(
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": "cgh proxy: the LiteLLM gateway is unreachable "
                        f"({type(exc).__name__})",
                    },
                },
                status_code=502,
            )

        chunks: list[bytes] = []

        async def relay():
            try:
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await resp.aclose()

        async def finish():
            await record(resp.status_code, resp.headers, b"".join(chunks))

        out_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in _HOP_HEADERS and k.lower() != "content-encoding"
        }
        return StreamingResponse(
            relay(),
            status_code=resp.status_code,
            headers=out_headers,
            background=BackgroundTask(finish),
        )

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    return Starlette(
        routes=[Route("/{path:path}", forward, methods=_METHODS)],
        lifespan=lifespan,
    )


def serve(cfg: ProxyConfig, port: int) -> None:
    import uvicorn

    uvicorn.run(build_app(cfg), host="127.0.0.1", port=port, log_level="warning")
