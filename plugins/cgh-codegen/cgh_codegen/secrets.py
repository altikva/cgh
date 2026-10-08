# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Built-in secret check run on every piece of file content
#              codegen is about to hand to a model, whatever the backend
#              and the egress posture. It does not depend on cgh-pii or on
#              findings recorded at index time: the patterns live here and
#              run on the text itself. A hit names the kind of secret and
#              its line, never the value. Precision over recall: the
#              patterns target fixed-shape credentials, and credential
#              assignments skip obvious placeholders and env lookups.

from __future__ import annotations

import re
from dataclasses import dataclass

# Fixed-shape credentials: a match is almost always real.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    # A bearer credential: 20+ token characters with at least one digit,
    # which leaves out "Bearer YOUR_ACCESS_TOKEN" style placeholders and
    # templated "${TOKEN}" / "{token}" values (braces are not token chars).
    (
        "bearer token",
        re.compile(r"\bBearer\s+(?=[A-Za-z0-9\-._~+/]*\d)[A-Za-z0-9\-._~+/]{20,}=*"),
    ),
)

# A GCP service-account key file: both markers in the same text.
_GCP_TYPE = re.compile(r"\"type\"\s*:\s*\"service_account\"")
_GCP_KEY = re.compile(r"\"private_key\"\s*:")

# name = "literal" (also "name": "literal" in JSON/YAML). The name may carry a
# prefix (db_password, GITHUB_TOKEN) and secret may be secret_key; the value
# must be a quoted literal, so env lookups (os.environ[...], getenv(...)) and
# bare references never match.
_ASSIGNMENT = re.compile(
    r"(?i)\b[\w-]*?(?:password|passwd|secret(?:[_-]?key)?|api[_-]?key|token)\b"
    r"[\"']?\s*[:=]\s*[\"']([^\"'\n]*)[\"']"
)

_PLACEHOLDER_WORDS = (
    "changeme",
    "change_me",
    "change-me",
    "example",
    "placeholder",
    "dummy",
    "redacted",
    "your_",
    "your-",
    "yourpassword",
    "replace",
    "fake",
    "sample",
    "todo",
)
_PLACEHOLDER_EXACT = {"password", "passwd", "secret", "token", "none", "null"}


@dataclass(frozen=True)
class SecretHit:
    kind: str
    line: int


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    low = v.lower()
    if len(v) < 8 or any(ch.isspace() for ch in v):
        return True
    if low in _PLACEHOLDER_EXACT or len(set(low)) <= 2:  # "xxxxxxxx", "********"
        return True
    if v.startswith(("$", "{{", "<", "%(", "{")) or "${" in v:
        return True
    return any(word in low for word in _PLACEHOLDER_WORDS)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def find_secrets(text: str) -> list[SecretHit]:
    """Every secret-shaped match in ``text``, in line order. Carries the
    kind and line only, never the matched value."""
    hits: list[SecretHit] = []
    for kind, pattern in _PATTERNS:
        hits.extend(
            SecretHit(kind, _line(text, m.start())) for m in pattern.finditer(text)
        )
    gcp_type = _GCP_TYPE.search(text)
    if gcp_type and _GCP_KEY.search(text):
        hits.append(SecretHit("GCP service account key", _line(text, gcp_type.start())))
    for m in _ASSIGNMENT.finditer(text):
        if not _is_placeholder(m.group(1)):
            hits.append(SecretHit("credential assignment", _line(text, m.start())))
    return sorted(set(hits), key=lambda h: (h.line, h.kind))


def secret_refusal(label: str, text: str) -> str | None:
    """None when ``text`` looks clean, else a reason naming ``label`` (a
    file path or "the spec"), the kind and the line of the first hit."""
    hits = find_secrets(text)
    if not hits:
        return None
    first = hits[0]
    more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
    return (
        f"{label}:{first.line} holds what looks like a {first.kind}{more}; "
        "codegen never sends secrets to a model. Move the secret out of the "
        "file (an environment variable) or pick another reference."
    )
