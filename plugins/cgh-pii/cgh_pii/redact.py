# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Text redaction: find PII spans (the regex tier, plus the
#              NER tier for names and locations when presidio is
#              installed), keep the categories the caller asked for, and
#              replace each span with a stable token. The finding store
#              keeps only counts, so redaction re-scans the text for the
#              actual spans. "person" and "location" need the NER extra;
#              the regex tier cannot see names. Two token styles:
#              numbered placeholders ([PERSON_1], distinct within the
#              doc) and keyed pseudonyms (<pii.person:hex>, correlatable
#              across documents that share a secret).

from __future__ import annotations

import hashlib
import hmac as _hmac
import re
import secrets

from .regex_scanner import (
    _AWS_KEY,
    _CARD,
    _EMAIL,
    _IBAN,
    _PHONE,
    _PRIVATE_KEY,
    _card_ok,
    _iban_ok,
)

# category -> (pattern, optional validator on the match object)
_REGEX = {
    "email": (_EMAIL, None),
    "phone": (_PHONE, None),
    "iban": (_IBAN, lambda m: _iban_ok(m.group(0))),
    "card": (_CARD, lambda m: _card_ok(re.sub(r"[ -]", "", m.group(0)))),
    "aws_key": (_AWS_KEY, None),
    "private_key": (_PRIVATE_KEY, None),
}
# NER entity type -> category
_NER = {"PERSON": "person", "LOCATION": "location"}
NER_CATEGORIES = frozenset(_NER.values())
ALL_CATEGORIES = frozenset(_REGEX) | NER_CATEGORIES


class RedactError(RuntimeError):
    """Redaction cannot proceed (a requested category needs the NER
    extra that is not installed)."""


def _regex_spans(text: str, cats: set[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for cat, (pattern, validate) in _REGEX.items():
        if cat not in cats:
            continue
        for m in pattern.finditer(text):
            if validate is not None and not validate(m):
                continue
            spans.append((m.start(), m.end(), cat))
    return spans


def _ner_spans(text: str, cats: set[str], language: str) -> list[tuple[int, int, str]]:
    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError as exc:
        wanted = ", ".join(sorted(cats & NER_CATEGORIES))
        raise RedactError(
            f'{wanted} needs the NER tier: pip install "cgh-pii[ner]"'
        ) from exc
    engine = AnalyzerEngine()
    entities = [e for e, c in _NER.items() if c in cats]
    spans: list[tuple[int, int, str]] = []
    for r in engine.analyze(text=text, entities=entities, language=language):
        if r.score < 0.5:
            continue
        cat = _NER.get(r.entity_type)
        if cat:
            spans.append((r.start, r.end, cat))
    return spans


def _propagate(
    text: str, ner: list[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """Every literal re-occurrence of a detected NER value, same
    category. Dedup handles the overlap with the original spans."""
    extra: list[tuple[int, int, str]] = []
    seen: set[tuple[str, str]] = set()
    for start, end, cat in ner:
        value = text[start:end]
        if len(value) < 3 or (cat, value) in seen:
            continue
        seen.add((cat, value))
        for m in re.finditer(re.escape(value), text):
            extra.append((m.start(), m.end(), cat))
    return extra


def _dedup(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Longest-wins over overlaps; then left to right."""
    spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, cat in spans:
        if start >= last_end:
            kept.append((start, end, cat))
            last_end = end
    return kept


def redact(
    text: str,
    only: list[str] | None = None,
    mode: str = "placeholder",
    secret: bytes | None = None,
    language: str = "en",
) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts_by_category).

    ``only`` limits the categories (default: all). ``mode`` is
    "placeholder" (numbered [PERSON_1], distinct within this text) or
    "pseudonym" (keyed <pii.person:hex>; pass a fixed ``secret`` for the
    same token across documents, else one is generated for this call).
    Raises RedactError if a requested NER category has no presidio."""
    cats = set(only) if only else set(ALL_CATEGORIES)
    unknown = cats - ALL_CATEGORIES
    if unknown:
        raise RedactError(f"unknown categor(y/ies): {', '.join(sorted(unknown))}")
    if mode not in ("placeholder", "pseudonym"):
        raise RedactError("mode must be 'placeholder' or 'pseudonym'")

    spans = _regex_spans(text, cats)
    if cats & NER_CATEGORIES:
        ner = _ner_spans(text, cats, language)
        # NER is probabilistic and misses repeat mentions: a name it
        # tagged once may recur untagged, which for redaction is a leak.
        # Propagate every distinct detected value to all its literal
        # occurrences, so "Jean Dupont" found once is redacted
        # everywhere. Regex spans are already exhaustive.
        spans += ner + _propagate(text, ner)
    spans = _dedup(spans)

    # Always have a key ready; placeholder mode just never uses it. One
    # key per call means the same value maps to the same token within
    # this document even when the caller passed none.
    key = secret if secret is not None else secrets.token_bytes(32)
    tokens: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    def _token(cat: str, value: str) -> str:
        cached = tokens.get((cat, value))
        if cached:
            return cached
        if mode == "pseudonym":
            digest = _hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[
                :10
            ]
            tok = f"<pii.{cat}:{digest}>"
        else:
            counters[cat] = counters.get(cat, 0) + 1
            tok = f"[{cat.upper()}_{counters[cat]}]"
        tokens[(cat, value)] = tok
        return tok

    # Number tokens in reading order (a forward pass), so [PERSON_1] is
    # the first name in the text. Then replace back to front to keep the
    # offsets of not-yet-replaced spans valid.
    forward = sorted(spans, key=lambda s: s[0])
    for start, end, cat in forward:
        _token(cat, text[start:end])
    counts: dict[str, int] = {}
    out = text
    for start, end, cat in reversed(forward):
        out = out[:start] + _token(cat, text[start:end]) + out[end:]
        counts[cat] = counts.get(cat, 0) + 1
    return out, counts
