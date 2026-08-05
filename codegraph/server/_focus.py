# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Intent-driven filtering for large tool results. When a
#              result overflows its cap, keep the items relevant to the
#              caller's focus instead of the arbitrary first N, and never
#              truncate silently: the returned note always says how many
#              were dropped and how to narrow further.

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def focus_filter(
    items: list[Any],
    focus: str,
    text_of: Callable[[Any], str],
    cap: int,
) -> tuple[list[Any], str | None]:
    """Return (kept, note).

    Under the cap: everything, no note. Over the cap with no focus: the
    first `cap`, with a note telling the caller a focus term would
    narrow it. Over the cap with a focus: the items whose text matches
    the focus terms (ranked by how many terms hit) win the cap, so a
    relevant item beyond position `cap` in the raw order survives.
    text_of(item) yields the string a focus term is matched against.
    """
    total = len(items)
    if total <= cap:
        return items, None

    focus = (focus or "").strip()
    if not focus:
        return (
            items[:cap],
            f"showing {cap} of {total}; pass focus=<term> to keep the "
            f"relevant ones instead of the first {cap}",
        )

    terms = [t.lower() for t in focus.split() if t]
    scored = [
        (sum(1 for t in terms if t in text_of(it).lower()), i, it)
        for i, it in enumerate(items)
    ]
    matched = [t for t in scored if t[0] > 0]
    if matched:
        matched.sort(key=lambda t: (-t[0], t[1]))  # more hits first, stable
        kept = [t[2] for t in matched[:cap]]
        note = (
            f"showing {len(kept)} of {total} matching focus={focus!r}; "
            f"{total - len(kept)} filtered out"
        )
        return kept, note
    return (
        items[:cap],
        f"focus={focus!r} matched none; showing the first {cap} of {total}",
    )
