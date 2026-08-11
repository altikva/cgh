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
# "other" is the catch-all the optional LLM tier maps its off-vocabulary
# hits into (id numbers, org names, credentials). Like NER values, LLM
# values are located by literal search, so they propagate to every
# occurrence in the document.
LLM_CATEGORY = "other"
_LITERAL_CATS = NER_CATEGORIES | {LLM_CATEGORY}
ALL_CATEGORIES = frozenset(_REGEX) | NER_CATEGORIES | {LLM_CATEGORY}


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


class _Tokenizer:
    """Shared token map so the same value maps to the same token across
    every chunk of one document. Numbering follows first-seen order."""

    def __init__(self, mode: str, key: bytes) -> None:
        self._mode = mode
        self._key = key
        self._map: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def token(self, cat: str, value: str) -> str:
        cached = self._map.get((cat, value))
        if cached:
            return cached
        if self._mode == "pseudonym":
            digest = _hmac.new(
                self._key, value.encode("utf-8"), hashlib.sha256
            ).hexdigest()[:10]
            tok = f"<pii.{cat}:{digest}>"
        else:
            self._counters[cat] = self._counters.get(cat, 0) + 1
            tok = f"[{cat.upper()}_{self._counters[cat]}]"
        self._map[(cat, value)] = tok
        return tok

    def known(self) -> list[tuple[str, str]]:
        return list(self._map)


def _validate(only: list[str] | None, mode: str) -> set[str]:
    cats = set(only) if only else set(ALL_CATEGORIES)
    unknown = cats - ALL_CATEGORIES
    if unknown:
        raise RedactError(f"unknown categor(y/ies): {', '.join(sorted(unknown))}")
    if mode not in ("placeholder", "pseudonym"):
        raise RedactError("mode must be 'placeholder' or 'pseudonym'")
    return cats


def _llm_spans(
    text: str, cats: set[str], llm_hits: list[tuple[str, str]] | None
) -> list[tuple[int, int, str]]:
    """(category, quote) pairs from the LLM tier located in ``text`` by
    literal search, filtered to the requested categories. A quote the
    model invented (absent verbatim) yields no span, so a hallucination
    can never redact the wrong bytes."""
    if not llm_hits:
        return []
    spans: list[tuple[int, int, str]] = []
    for cat, quote in llm_hits:
        if cat not in cats or not quote:
            continue
        for m in re.finditer(re.escape(quote), text):
            spans.append((m.start(), m.end(), cat))
    return spans


def _all_spans(
    text: str,
    cats: set[str],
    language: str,
    llm_hits: list[tuple[str, str]] | None = None,
) -> list[tuple[int, int, str]]:
    spans = _regex_spans(text, cats)
    if cats & NER_CATEGORIES:
        ner = _ner_spans(text, cats, language)
        # NER is probabilistic and misses repeat mentions: a name it
        # tagged once may recur untagged, which for redaction is a leak.
        # Propagate every distinct detected value to all its literal
        # occurrences, so "Jean Dupont" found once is redacted
        # everywhere. Regex spans are already exhaustive.
        spans += ner + _propagate(text, ner)
    spans += _llm_spans(text, cats, llm_hits)
    return _dedup(spans)


def _apply(text: str, spans: list[tuple[int, int, str]], tk: _Tokenizer) -> str:
    """Replace back to front to keep not-yet-replaced offsets valid."""
    out = text
    for start, end, cat in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + tk.token(cat, text[start:end]) + out[end:]
    return out


def redact(
    text: str,
    only: list[str] | None = None,
    mode: str = "placeholder",
    secret: bytes | None = None,
    language: str = "en",
    llm_hits: list[tuple[str, str]] | None = None,
) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts_by_category).

    ``only`` limits the categories (default: all). ``mode`` is
    "placeholder" (numbered [PERSON_1], distinct within this text) or
    "pseudonym" (keyed <pii.person:hex>; pass a fixed ``secret`` for the
    same token across documents, else one is generated for this call).
    ``llm_hits`` are (redaction_category, quote) pairs from the optional
    LLM tier; each quote is located by literal search and redacted like a
    NER value. Raises RedactError if a requested NER category has no
    presidio."""
    out, counts = redact_chunks([text], only, mode, secret, language, llm_hits)
    return out[0], counts


def redact_chunks(
    chunks: list[str],
    only: list[str] | None = None,
    mode: str = "placeholder",
    secret: bytes | None = None,
    language: str = "en",
    llm_hits: list[tuple[str, str]] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Redact several pieces of one document with shared tokens, so the
    same value gets the same token everywhere and names detected in any
    chunk are redacted in all of them. Used for docx paragraphs and
    table cells; single-text callers use redact()."""
    cats = _validate(only, mode)
    # Always have a key ready; placeholder mode just never uses it.
    key = secret if secret is not None else secrets.token_bytes(32)
    tk = _Tokenizer(mode, key)

    # Pass 1: number tokens in document reading order over the whole text.
    full = "\n".join(chunks)
    for start, end, cat in sorted(
        _all_spans(full, cats, language, llm_hits), key=lambda s: s[0]
    ):
        tk.token(cat, full[start:end])

    # Pass 2: redact each chunk. Regex re-detected per chunk; every known
    # literal value (NER names, LLM quotes) replaced by literal search, so
    # a value found once lands in every chunk. Tokens come from the shared
    # map.
    known_literal = [(c, v) for (c, v) in tk.known() if c in _LITERAL_CATS]
    out_chunks: list[str] = []
    counts: dict[str, int] = {}
    for chunk in chunks:
        spans = _regex_spans(chunk, cats)
        for cat, value in known_literal:
            for m in re.finditer(re.escape(value), chunk):
                spans.append((m.start(), m.end(), cat))
        spans = _dedup(spans)
        for _s, _e, cat in spans:
            counts[cat] = counts.get(cat, 0) + 1
        out_chunks.append(_apply(chunk, spans, tk))
    return out_chunks, counts
