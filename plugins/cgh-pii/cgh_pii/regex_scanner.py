# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex scanner for secrets and, optionally, PII. Deterministic
#              and fast; used by `cgh pii scan` and, with scan_on_index,
#              on the indexing hot path. pii=False keeps only the secret
#              patterns, which are precise; the PII ones are noisy on real
#              repos and are opt-in through the callers. Card candidates must
#              pass Luhn and IBAN candidates must pass mod 97, so random
#              digit runs do not flag files. Finding values carry only the
#              match count, never the matched data: findings feed the FTS
#              and must not spread what they detect.

from __future__ import annotations

import re
from pathlib import Path

from codegraph.plugin_api import ScanFinding

_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_PHONE = re.compile(r"(?<![\w./-])(?:\+|00)\d{1,3}[\s.-]?\d(?:[\s.-]?\d){6,11}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

# Provider API tokens. Each pattern is a fixed prefix followed by a single
# character class, so a match is linear in the input and cannot backtrack
# catastrophically; keep new entries to that shape. The lookbehind makes
# the prefix start a word, so a token embedded in a longer identifier does
# not match.
_WORD_START = r"(?<![A-Za-z0-9_-])"
_API_TOKENS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "secret.anthropic_key",
        re.compile(_WORD_START + r"sk-ant-[a-z]{3,5}[0-9]{2}-[A-Za-z0-9_-]{32,}"),
    ),
    (
        "secret.openai_key",
        re.compile(_WORD_START + r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{32,}"),
    ),
    (
        "secret.github_token",
        re.compile(
            _WORD_START + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"
        ),
    ),
    ("secret.gitlab_token", re.compile(_WORD_START + r"glpat-[A-Za-z0-9_-]{20,}")),
    ("secret.slack_token", re.compile(_WORD_START + r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("secret.google_api_key", re.compile(_WORD_START + r"AIza[0-9A-Za-z_-]{35}")),
    ("secret.stripe_key", re.compile(_WORD_START + r"[rs]k_live_[A-Za-z0-9]{24,}")),
)
# A secret assigned to a known name, quoted (`password = "..."`) or bare
# as in a .env file or a shell line (`PASSWORD=hunter2hunter2`). The
# "value" group is the secret itself: redaction masks only that part and
# keeps the variable name readable. A bare value runs to the next
# whitespace, stays on the name's line and never ends on a comma or a
# semicolon, so a keyword argument in code does not match. It is skipped
# when it is a reference rather than a secret: `$VAR` or `${VAR}`, a
# `<placeholder>`, an already masked `****`, or an attribute access such
# as `settings.API_KEY`; quotes, brackets and parentheses rule out code.
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\b"
    r"(?:\s*[:=]\s*(?P<quote>[\"'])"
    r"|[ \t]*[:=][ \t]*(?![$<])(?!\*+(?!\S))(?![A-Za-z_]\w*(?:\.\w+)+(?!\S)))"
    r"(?P<value>(?(quote)[^\"']{8,}|[^\s\"'`(){}\[\]]{7,}[^\s\"'`(){}\[\],;]))"
    r"(?(quote)[\"']|(?!\S))"
)

# Every secret.* kind, as (key, severity, pattern), in report order. The
# scanner and the redactor both read this table, so a new secret kind is
# detected and redacted from one entry. A pattern with a "value" group is
# redacted on that group only.
_SECRETS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("secret.aws_key", "block", _AWS_KEY),
    ("secret.private_key", "block", _PRIVATE_KEY),
    *((key, "block", pattern) for key, pattern in _API_TOKENS),
    ("secret.assignment", "warn", _ASSIGNMENT),
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_ok(candidate: str) -> bool:
    rearranged = candidate[4:] + candidate[:4]
    converted = "".join(str(int(ch, 36)) if ch.isalpha() else ch for ch in rearranged)
    try:
        return int(converted) % 97 == 1
    except ValueError:
        return False


class RegexPiiScanner:
    """Inline scanner producing secret.* findings, plus pii.* ones when
    ``pii`` is true (the default here, so direct callers such as the
    bugreport tripwire keep the full pattern set; the plugin's config and
    CLI default it to false)."""

    name = "pii-regex"
    deferred = False

    def __init__(self, disabled_keys: set[str] | None = None, pii: bool = True) -> None:
        self._disabled = disabled_keys or set()
        self._pii = pii

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        found: list[ScanFinding] = []

        def _emit(key: str, severity: str, matches: list[int]) -> None:
            if matches and key not in self._disabled:
                found.append(
                    ScanFinding(
                        key=key,
                        value=str(len(matches)),
                        line=matches[0],
                        severity=severity,
                    )
                )

        if self._pii:
            _emit("pii.email", "warn", _match_lines(_EMAIL, text))
            _emit(
                "pii.phone",
                "warn",
                _match_lines(_PHONE, text, validate=lambda m: _phone_ok(m.group(0))),
            )
            _emit(
                "pii.iban",
                "warn",
                _match_lines(_IBAN, text, validate=lambda m: _iban_ok(m.group(0))),
            )
            _emit(
                "pii.card",
                "warn",
                _match_lines(
                    _CARD,
                    text,
                    validate=lambda m: _card_ok(re.sub(r"[ -]", "", m.group(0))),
                ),
            )
        for key, severity, pattern in _SECRETS:
            _emit(key, severity, _match_lines(pattern, text))
        return found


# Real card networks: first digit 3 (Amex/Diners/JCB), 4 (Visa),
# 5 (Mastercard), 6 (Discover/Maestro). A 13-19 digit run that starts
# elsewhere, or is a single repeated digit or a straight run, is almost
# always a coincidental Luhn pass in an id, coordinate or hash, not a PAN.
_CARD_IIN = frozenset("3456")


def _is_monotone(digits: str) -> bool:
    """A strictly ascending or descending consecutive run (1234..., 9876...):
    a filler value, never a real card even when it passes Luhn."""
    from itertools import pairwise

    steps = {int(b) - int(a) for a, b in pairwise(digits)}
    return steps in ({1}, {-1})


def _card_ok(digits: str) -> bool:
    if not (13 <= len(digits) <= 19):
        return False
    if digits[0] not in _CARD_IIN:
        return False
    if len(set(digits)) == 1 or _is_monotone(digits):
        return False
    return _luhn_ok(digits)


def _phone_ok(match: str) -> bool:
    """The regex accepts any `+`/`00` prefixed digit-and-separator run;
    keep only plausible E.164 numbers (8 to 15 digits) and drop runs that
    are mostly separators (single digits spaced out, as diagram text
    renders a coordinate list), which are noise, not phone numbers."""
    digits = re.sub(r"\D", "", match)
    if not (8 <= len(digits) <= 15):
        return False
    seps = sum(1 for ch in match if ch in " .-")
    # A real number groups digits; more separators than digits means the
    # run is a spaced sequence, not a phone.
    return seps <= len(digits) // 2


def _match_lines(pattern: re.Pattern, text: str, validate=None) -> list[int]:
    """1-based line numbers of every (validated) match."""
    lines: list[int] = []
    for m in pattern.finditer(text):
        if validate is not None and not validate(m):
            continue
        lines.append(text.count("\n", 0, m.start()) + 1)
    return lines
