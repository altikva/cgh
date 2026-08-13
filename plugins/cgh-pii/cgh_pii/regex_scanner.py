# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Inline regex scanner for PII and secrets. Deterministic and
#              fast enough for the indexing hot path. Card candidates must
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
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"
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
    """Inline scanner producing pii.* and secret.* findings."""

    name = "pii-regex"
    deferred = False

    def __init__(self, disabled_keys: set[str] | None = None) -> None:
        self._disabled = disabled_keys or set()

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
        _emit("secret.aws_key", "block", _match_lines(_AWS_KEY, text))
        _emit("secret.private_key", "block", _match_lines(_PRIVATE_KEY, text))
        _emit("secret.assignment", "warn", _match_lines(_ASSIGNMENT, text))
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
