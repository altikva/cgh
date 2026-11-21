# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deterministic core of `decide`: from knowledge search hits to a
#              recommendation with its evidence. Keeps the kinds that record
#              a choice, splits the hits into supporting, against (dead ends
#              and corrected entries) and untrusted (shared stores), orders
#              the supporting ones by tier, detects a rival of equal standing
#              and derives a confidence with the reason for it. The
#              recommendation is the best precedent, quoted: no model, no
#              generated answer. Pure functions, no SQL and no MCP: the hits
#              come from cgh.state.call_log.

from __future__ import annotations

import json
import time
from collections.abc import Callable

# Kinds that record a choice or a rule. Notes and glossary entries describe,
# they do not decide.
KEPT_KINDS = frozenset(
    {"decision", "standing_instruction", "pattern", "gotcha", "style"}
)
# A session digest mentions everything and decides nothing.
DIGEST_TAG = "session-digest"

# Outcome statuses of a false trail already paid for.
AGAINST_STATUS = frozenset({"dead_end", "corrected"})
_PROVEN_STATUS = frozenset({"preferred", "useful"})

# Characters of the recommended entry's body quoted as `statement`, and of an
# evidence entry's body quoted as `excerpt`.
STATEMENT_CHARS = 600
EXCERPT_CHARS = 160
# The statement is never clipped under this many characters to fit a budget.
_MIN_STATEMENT_CHARS = 200

_MATCH_ORDER = ("high", "medium", "low")

_TIER_STANDING, _TIER_PREFERRED, _TIER_USEFUL, _TIER_UNRATED, _TIER_CONTESTED = range(5)


def status_of(hit: dict) -> str | None:
    """Outcome status of a hit, None when it was never rated."""
    outcome = hit.get("outcome")
    if isinstance(outcome, dict):
        return outcome.get("status") or None
    return None


def match_of(hit: dict) -> str:
    """How well the hit matches the question: high, medium or low. A hit the
    search did not grade counts as low, the cautious reading."""
    grade = hit.get("confidence")
    return grade if grade in _MATCH_ORDER else "low"


def best_match(*grades: str | None) -> str:
    """The strongest of several match grades; low when none is known."""
    for grade in _MATCH_ORDER:
        if grade in grades:
            return grade
    return "low"


def is_candidate(hit: dict) -> bool:
    """Whether a hit can be a precedent: a kept kind that is not a digest."""
    tags = hit.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return hit.get("kind") in KEPT_KINDS and DIGEST_TAG not in tags


def is_shared(hit: dict) -> bool:
    """Whether a hit is only known from a shared store. A shared hit this repo
    also holds (``local_copy``) counts as local: the repo can rate its copy."""
    return bool(hit.get("untrusted")) and not hit.get("local_copy")


def split(hits: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(supporting, against, untrusted), each in the order received. Hits that
    cannot be a precedent are dropped."""
    supporting: list[dict] = []
    against: list[dict] = []
    untrusted: list[dict] = []
    for hit in hits:
        if not is_candidate(hit):
            continue
        if status_of(hit) in AGAINST_STATUS:
            against.append(hit)
        elif is_shared(hit):
            untrusted.append(hit)
        else:
            supporting.append(hit)
    return supporting, against, untrusted


def tier(hit: dict) -> int:
    """Standing of a supporting hit, lower first: a durable rule of the user,
    then an entry confirmed several times, confirmed once, never rated (or
    whose verdicts faded), and last one whose verdicts disagree."""
    if hit.get("kind") == "standing_instruction":
        return _TIER_STANDING
    status = status_of(hit)
    if status == "preferred":
        return _TIER_PREFERRED
    if status == "useful":
        return _TIER_USEFUL
    if status == "contested":
        return _TIER_CONTESTED
    return _TIER_UNRATED


def rank(supporting: list[dict]) -> list[dict]:
    """Supporting hits in recommendation order. A low match never precedes a
    better one, whatever its standing; then the tier; then the order received,
    which is the search's own score order."""
    keyed = sorted(
        enumerate(supporting),
        key=lambda pair: (match_of(pair[1]) == "low", tier(pair[1]), pair[0]),
    )
    return [pair[1] for pair in keyed]


def rivals(ranked: list[dict]) -> list[dict]:
    """Supporting hits of the same tier and match grade as the head. The
    search returns live entries only, so none of them supersedes the head: two
    live precedents of equal standing mean the store has not settled."""
    if not ranked:
        return []
    head = ranked[0]
    key = (tier(head), match_of(head))
    return [h for h in ranked[1:] if (tier(h), match_of(h)) == key]


def _standing_words(hit: dict) -> str:
    if hit.get("kind") == "standing_instruction":
        return "a standing instruction"
    status = status_of(hit)
    if status in (None, "faded"):
        return "never rated" if status is None else "verdicts faded"
    return f"outcome {status}"


def confidence(
    ranked: list[dict], against: list[dict], untrusted: list[dict]
) -> tuple[str, str]:
    """(confidence, basis) from the head of the ranked supporting hits.

    high: the head matches high and is a standing instruction or has a
    preferred or useful outcome, with no rival. medium: the same with a rival;
    or a high match never rated; or a medium match with a preferred or useful
    outcome. low: supporting hits exist but none of that holds, or the only
    evidence is shared or refuted. none: no candidate at all.
    """
    if not ranked:
        if against or untrusted:
            parts = []
            if against:
                parts.append(f"{len(against)} refuted")
            if untrusted:
                parts.append(f"{len(untrusted)} from a shared store")
            return "low", "no local precedent stands: " + ", ".join(parts)
        return "none", "no precedent found"
    head = ranked[0]
    match = match_of(head)
    status = status_of(head)
    proven = head.get("kind") == "standing_instruction" or status in _PROVEN_STATUS
    rival_count = len(rivals(ranked))
    rival_words = (
        "no rival"
        if not rival_count
        else f"{rival_count} rival{'s' if rival_count > 1 else ''} of equal standing"
    )
    basis = f"match {match}, {_standing_words(head)}, {rival_words}"
    if match == "high" and proven:
        return ("medium" if rival_count else "high"), basis
    if match == "high" and status in (None, "faded"):
        return "medium", basis
    if match == "medium" and status in _PROVEN_STATUS:
        return "medium", basis
    return "low", basis


def _clip(text: str, limit: int) -> str:
    from cgh.state.knowledge_recall import clip

    return clip(text, limit)[0]


def _recorded(hit: dict) -> str | None:
    try:
        ts = float(hit.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def _precedent(hit: dict, with_statement: bool) -> dict:
    out: dict = {
        "entry_id": hit.get("id"),
        "title": hit.get("title"),
        "kind": hit.get("kind"),
    }
    if with_statement:
        out["statement"] = _clip(str(hit.get("body") or ""), STATEMENT_CHARS)
    recorded = _recorded(hit)
    if recorded:
        out["recorded"] = recorded
    if isinstance(hit.get("outcome"), dict):
        out["outcome"] = hit["outcome"]
    return out


def _evidence(hit: dict, excerpt: bool = True) -> dict:
    out: dict = {
        "id": hit.get("id"),
        "title": hit.get("title"),
        "kind": hit.get("kind"),
        "match": match_of(hit),
    }
    status = status_of(hit)
    if status:
        out["outcome"] = {"status": status}
    if excerpt:
        out["excerpt"] = _clip(str(hit.get("body") or ""), EXCERPT_CHARS)
    return out


def _against_evidence(hit: dict) -> dict:
    out = _evidence(hit)
    outcome = hit.get("outcome") or {}
    if outcome.get("last_note"):
        out["outcome"]["last_note"] = outcome["last_note"]
    if outcome.get("corrected_by"):
        out["corrected_by"] = outcome["corrected_by"]
    return out


def _untrusted_evidence(hit: dict) -> dict:
    out = _evidence(hit)
    out["namespace"] = hit.get("namespace")
    out["origin_repo"] = hit.get("origin_repo")
    return out


def build_answer(
    question: str,
    hits: list[dict],
    limit: int = 5,
    suggestion: dict | None = None,
) -> dict:
    """The `decide` answer for ``question`` from search hits, local ones in
    score order then shared ones. ``limit`` caps each evidence list.
    ``suggestion`` (the knowledge_record call to make once the question is
    settled) is carried only when nothing is recommended."""
    limit = max(1, int(limit))
    supporting, against, untrusted = split(hits)
    ranked = rank(supporting)
    level, basis = confidence(ranked, against, untrusted)
    recommended = ranked[0] if ranked and level in ("high", "medium") else None
    alternatives = rivals(ranked)
    answer: dict = {
        "question": question,
        "recommendation": _precedent(recommended, True) if recommended else None,
        "confidence": level,
        "basis": basis,
    }
    if alternatives:
        answer["contested"] = True
    answer["alternatives"] = [_precedent(h, False) for h in alternatives[:limit]]
    answer["evidence"] = {
        "supporting": [
            # The recommended entry is already quoted whole as `statement`.
            _evidence(h, excerpt=h is not recommended)
            for h in ranked[:limit]
        ],
        "against": [_against_evidence(h) for h in against[:limit]],
        "untrusted": [_untrusted_evidence(h) for h in untrusted[:limit]],
    }
    if recommended is None:
        if suggestion is not None:
            answer["suggestion"] = suggestion
    else:
        entry_id = recommended.get("id")
        answer["next"] = (
            "After acting on it, say whether the precedent held: "
            f'knowledge_outcome(entry_id={entry_id}, verdict="useful") '
            '(or "dead_end", "corrected").'
        )
    return answer


def _json_bytes(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def fit_answer(
    answer: dict, target: int, measure: Callable[[dict], int] | None = None
) -> None:
    """Bring an answer near ``target`` bytes, in place. Evidence goes first,
    from the tail: the excerpts, then whole entries (shared ones, then
    supporting ones after the first, then refuted ones after the first), the
    count left out in ``dropped``. The recommendation's statement is clipped
    last. Any entry is one knowledge_list(entry_id=...) call away."""
    size = measure or _json_bytes
    evidence = answer.get("evidence") or {}
    lists = [evidence.get(k) or [] for k in ("supporting", "against", "untrusted")]
    for items in reversed(lists):
        for item in reversed(items):
            if size(answer) <= target:
                return
            item.pop("excerpt", None)
    dropped = 0
    for items in (lists[2], lists[0], lists[1]):
        keep = 0 if items is lists[2] else 1
        while len(items) > keep and size(answer) > target:
            items.pop()
            dropped += 1
    if dropped:
        answer["dropped"] = dropped
    rec = answer.get("recommendation")
    for _ in range(3):
        excess = size(answer) - target
        if not rec or excess <= 0:
            break
        statement = str(rec.get("statement") or "")
        keep_chars = max(_MIN_STATEMENT_CHARS, len(statement) - excess - 16)
        if keep_chars >= len(statement):
            break
        rec["statement"] = _clip(statement, keep_chars)
