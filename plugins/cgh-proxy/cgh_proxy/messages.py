# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Reading the Anthropic Messages payloads that cross the proxy.
#              anatomy         where the bytes of a request go: system,
#                              tools, messages, and per content block type
#              outbound_text   the text a request sends out, whole and for
#                              its last message only (what this turn adds)
#              response_facts  model, stop reason and token usage from a
#                              JSON answer or a reassembled SSE stream
#              scan_outbound   PII and secret finding counts from the cgh-pii
#                              regex scanner, never values

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


def _size(value) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def anatomy(body: dict) -> dict:
    """Byte counts of a /v1/messages request, by part and by block type."""
    by_type: Counter[str] = Counter()
    messages = body.get("messages") or []
    for msg in messages:
        if isinstance(msg, dict):
            for block in _blocks(msg.get("content")):
                by_type[str(block.get("type", "?"))] += _size(block)
    last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
    tools = body.get("tools") or []
    return {
        "model": body.get("model"),
        "stream": bool(body.get("stream")),
        "max_tokens": body.get("max_tokens"),
        "system_bytes": _size(body["system"]) if body.get("system") else 0,
        "tools": len(tools),
        "tools_bytes": _size(tools) if tools else 0,
        "messages": len(messages),
        "messages_bytes": _size(messages) if messages else 0,
        "block_bytes": dict(by_type.most_common()),
        "last_role": last.get("role"),
        "last_bytes": _size(last.get("content", "")) if last else 0,
    }


def _block_text(block: dict) -> str:
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text", ""))
    if kind == "tool_result":
        return "\n".join(_block_text(b) for b in _blocks(block.get("content")))
    if kind == "tool_use":
        return json.dumps(block.get("input", {}), ensure_ascii=False)
    return ""  # images, thinking signatures: no text to scan


def _message_text(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    return "\n".join(_block_text(b) for b in _blocks(msg.get("content")))


def outbound_text(body: dict) -> tuple[str, str]:
    """(everything the request sends, its last message alone)."""
    parts = [_message_text({"content": body.get("system")})]
    messages = body.get("messages") or []
    parts += [_message_text(m) for m in messages]
    last = _message_text(messages[-1]) if messages else ""
    return "\n".join(p for p in parts if p), last


def _merge_usage(usage: dict, extra) -> None:
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(value, int):
                usage[key] = value


def response_facts(content_type: str, text: str) -> dict:
    """Model, stop reason and usage from the answer, streamed or not."""
    facts: dict = {"usage": {}}
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "message_start":
                message = event.get("message") or {}
                facts["model"] = message.get("model")
                _merge_usage(facts["usage"], message.get("usage"))
            elif event.get("type") == "message_delta":
                facts["stop_reason"] = (event.get("delta") or {}).get("stop_reason")
                _merge_usage(facts["usage"], event.get("usage"))
            elif event.get("type") == "error":
                facts["error"] = (event.get("error") or {}).get("type")
        return facts
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        return facts
    if isinstance(body, dict):
        facts["model"] = body.get("model")
        facts["stop_reason"] = body.get("stop_reason")
        _merge_usage(facts["usage"], body.get("usage"))
        if body.get("type") == "error":
            facts["error"] = (body.get("error") or {}).get("type")
    return facts


def _counts(scanner, text: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for finding in scanner.scan(Path("proxy-request.txt"), text, None):
        try:
            counts[finding.key] += int(finding.value)
        except (TypeError, ValueError):
            counts[finding.key] += 1
    return dict(counts)


def scan_outbound(body: dict) -> dict:
    """Finding counts for the whole request and for its last message.
    Only keys and counts leave this function: the journal must not
    spread what it detects."""
    try:
        from cgh_pii.regex_scanner import RegexPiiScanner
    except ImportError:
        return {"skipped": "cgh-pii not installed"}
    scanner = RegexPiiScanner(pii=True)
    whole, last = outbound_text(body)
    try:
        return {
            "request": _counts(scanner, whole),
            "last_message": _counts(scanner, last),
        }
    except Exception as exc:  # observation only: never fail the exchange
        log.warning("PII scan failed: %s", type(exc).__name__)
        return {"skipped": type(exc).__name__}
