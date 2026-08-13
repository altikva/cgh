# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Optional LLM tier for PII detection. Sends file text to a
#              local Ollama or an OpenAI-compatible endpoint and asks for
#              the PII the regex and NER tiers miss (names in odd formats,
#              quasi-identifiers, addresses, context-bound identifiers).
#              Sending content to a NON-loopback endpoint is egress: it is
#              refused unless [plugin.pii] pii_llm_allow_remote is set, and
#              every probe, allowed or denied, is written to the activity
#              log. A loopback endpoint stays on the machine and is free.
#              The reply is constrained to a fixed category vocabulary and
#              a JSON contract; anything off-contract is dropped.

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

# The categories the model may return. A hit outside this set is dropped,
# the same way the vision inventory enforces its vocabulary.
LLM_CATEGORIES = frozenset(
    {
        "person",
        "location",
        "org",
        "email",
        "phone",
        "id_number",
        "address",
        "credential",
        "other",
    }
)

_TIMEOUT = 60
_MAX_CHARS = 100_000  # a probe of a multi-MB blob is pointless and slow

# Auto-pick preference when the configured Ollama model is not installed.
# Embedding models cannot generate and are excluded.
_MODEL_PREF = ("qwen", "gemma", "llama", "mistral", "phi", "granite")
_TAGS_TTL = 60.0
_tags_cache: dict[str, tuple[float, frozenset[str]]] = {}


def installed_ollama_models(url: str) -> frozenset[str]:
    """Model names Ollama reports at <url>/api/tags, cached briefly. Empty
    set on any error."""
    import time

    now = time.time()
    hit = _tags_cache.get(url)
    if hit and now - hit[0] < _TAGS_TTL:
        return hit[1]
    names: set[str] = set()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        for m in data.get("models", []):
            n = str(m.get("name") or m.get("model") or "").strip()
            if n:
                names.add(n)
    except Exception:
        return frozenset()
    result = frozenset(names)
    _tags_cache[url] = (now, result)
    return result


def resolve_ollama_model(url: str, configured: str) -> str | None:
    """The Ollama model to call: the configured one when installed, else an
    auto-picked installed generative model, else None."""
    installed = installed_ollama_models(url)
    if not installed:
        return None
    if configured and configured in installed:
        return configured
    generative = sorted(m for m in installed if "embed" not in m.lower())
    if not generative:
        return None
    for fam in _MODEL_PREF:
        for m in generative:
            if m.lower().startswith(fam):
                return m
    return generative[0]


_PROMPT = (
    "You are a PII detector. Find every piece of personally identifiable or "
    "sensitive information in the TEXT below: names of people, locations, "
    "organizations tied to a person, emails, phone numbers, identifier "
    "numbers, postal addresses, and credentials. Focus on what a simple "
    "regex would miss (unusual formats, quasi-identifiers, context-bound "
    "identifiers). Reply with ONLY a JSON array; each item is "
    '{"category": <one of: person, location, org, email, phone, id_number, '
    'address, credential, other>, "quote": <the exact substring from the '
    "text>}. No prose, no markdown, no code fence. If there is none, reply "
    "[].\n\nTEXT:\n"
)


class LlmProbeError(RuntimeError):
    """The LLM probe was refused (egress policy) or could not run."""


def _endpoint(config: dict) -> tuple[str, str]:
    """(kind, url) for the active backend. An OpenAI-compatible endpoint
    wins when configured; otherwise Ollama (default loopback)."""
    base = str(config.get("llm_openai_base_url", "")).strip()
    if base:
        return "openai", base.rstrip("/")
    return "ollama", str(config.get("llm_ollama_url", "http://127.0.0.1:11434")).rstrip(
        "/"
    )


def egress_class(config: dict) -> str:
    """ "local" only for a loopback endpoint. Anything else is "cloud" and
    must clear the gate. Computed from the active URL, never a static
    label, so a configurable host cannot smuggle content out unchecked."""
    from codegraph.plugin_api import is_loopback_url

    _, url = _endpoint(config)
    return "local" if is_loopback_url(url) else "cloud"


def _guard_egress(config: dict, repo_root, file_path: str) -> None:
    """A loopback probe never leaves the machine and is free. A cloud
    endpoint is egress: allowed only when pii_llm_allow_remote is set, and
    audited either way. Fails closed: an unresolvable posture denies."""
    from codegraph.plugin_api import activity_log

    if egress_class(config) == "local":
        return
    if not config.get("pii_llm_allow_remote", False):
        activity_log(
            repo_root,
            "pii_llm_refused",
            f"cloud endpoint, pii_llm_allow_remote off: {file_path}",
        )
        raise LlmProbeError(
            "the PII LLM endpoint is not loopback, so a probe would send file "
            "content off the machine; set [plugin.pii] pii_llm_allow_remote = "
            "true to permit it (every probe is audited)"
        )
    _, url = _endpoint(config)
    activity_log(repo_root, "pii_llm_probe", f"{url} <- {file_path}")


def available(config: dict) -> bool:
    """Is the configured backend reachable? A cheap connect check for
    Ollama; presence of a base_url + model for OpenAI-compatible."""
    kind, url = _endpoint(config)
    if kind == "openai":
        return bool(url) and bool(config.get("llm_openai_model"))
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 11434
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.3):
            pass
    except OSError:
        return False
    # Reachable is not enough: an Ollama with no pulled model cannot probe.
    return (
        resolve_ollama_model(url, str(config.get("llm_model", "qwen2.5:3b")))
        is not None
    )


def _post_json(url: str, payload: bytes, headers: dict) -> dict:
    """POST JSON and parse the JSON reply. The scheme is pinned to
    http/https here (the endpoint URL comes from config, so a file:/
    custom scheme must never reach urlopen), which is also what makes the
    single urlopen below safe to allowlist for S310."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise LlmProbeError(f"refusing a non-http(s) LLM endpoint: {url!r}")
    req = urllib.request.Request(url, data=payload, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode()))


def _call(text: str, config: dict) -> str:
    kind, url = _endpoint(config)
    prompt = _PROMPT + text[:_MAX_CHARS]
    if kind == "openai":
        import os

        key = os.environ.get(
            str(config.get("llm_openai_api_key_env", "OPENAI_API_KEY"))
        )
        payload = json.dumps(
            {
                "model": config["llm_openai_model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = _post_json(url + "/chat/completions", payload, headers)
        return str(data["choices"][0]["message"]["content"])
    model = resolve_ollama_model(url, str(config.get("llm_model", "qwen2.5:3b")))
    if model is None:
        return ""  # no installed model: probe() turns this into no findings
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode()
    data = _post_json(
        url + "/api/generate", payload, {"Content-Type": "application/json"}
    )
    return str(data.get("response", ""))


def _parse(reply: str) -> list[tuple[str, str]]:
    """(category, quote) pairs from the model reply, tolerant of a stray
    code fence or leading prose. Off-vocabulary categories and empty
    quotes are dropped."""
    s = reply.strip()
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[tuple[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category", "")).strip().lower()
        quote = str(it.get("quote", "")).strip()
        if cat in LLM_CATEGORIES and quote:
            out.append((cat, quote))
    return out


def probe(
    text: str, config: dict, repo_root, file_path: str = ""
) -> list[tuple[str, str]]:
    """Ask the LLM for PII in ``text``. Returns (category, quote) pairs
    validated against the vocabulary. Raises LlmProbeError when the egress
    gate denies a cloud probe. A backend or parse failure returns []: the
    LLM tier is a best-effort addition, never a hard dependency of a scan
    or a redaction."""
    _guard_egress(config, repo_root, str(file_path) or "<memory>")
    if not text.strip():
        return []
    try:
        reply = _call(text, config)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError):
        return []
    return _parse(reply)


def quote_spans(text: str, hits: list[tuple[str, str]]) -> list[tuple[int, int, str]]:
    """Turn (category, quote) pairs into (start, end, category) spans by
    locating every literal occurrence of each quote in ``text``. A quote
    the model invented (not present verbatim) contributes nothing, so a
    hallucinated span can never redact the wrong bytes."""
    import re

    spans: list[tuple[int, int, str]] = []
    for cat, quote in hits:
        if not quote:
            continue
        for m in re.finditer(re.escape(quote), text):
            spans.append((m.start(), m.end(), cat))
    return spans


# Redaction maps the LLM vocabulary onto the redactor's category set;
# categories the redactor already owns keep their token, the rest fold
# into "other". Kept here so the scanner and the redactor agree.
def redaction_category(llm_cat: str) -> str:
    return {
        "person": "person",
        "location": "location",
        "address": "location",
        "email": "email",
        "phone": "phone",
        "id_number": "other",
        "org": "other",
        "credential": "other",
        "other": "other",
    }.get(llm_cat, "other")


def probe_file(path: Path, config: dict, repo_root) -> list[tuple[str, str]]:
    """Read ``path`` and probe it. Used by the on-demand CLI and the
    scanner. Non-text or unreadable files return []."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    return probe(text, config, repo_root, str(path))
