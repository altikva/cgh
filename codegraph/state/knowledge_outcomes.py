# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deterministic scoring of knowledge outcomes. An outcome says
#              whether relying on a knowledge entry, or on a file, worked:
#              useful, dead_end, or corrected (the entry was wrong and was
#              fixed). Each verdict weighs 0.5 ** (age / half-life), so old
#              verdicts fade. score_outcomes() turns a target's verdicts into
#              a status (preferred, useful, contested, dead_end, corrected,
#              faded) plus a signed score; rank_factor() and list_bucket()
#              turn that into search and list ordering. Pure functions, no
#              SQL: the rows come from codegraph.state.call_log.

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

VERDICTS = ("useful", "dead_end", "corrected")

# Days after which a verdict weighs half as much as a fresh one. Overridden by
# [codegraph] knowledge_half_life_days in config.toml.
DEFAULT_HALF_LIFE_DAYS = 30.0

# Below this total weight (three half-lives for a single verdict) the verdicts
# are too old to steer anything and the target reads as "faded". A correction
# never fades: the entry was wrong, however long ago that was found.
_MIN_WEIGHT = 0.125

# Share of the decayed weight that has to be useful (or negative) for a clear
# verdict; anything between is contested.
_CLEAR_SHARE = 0.75

# Useful verdicts needed before an entry counts as preferred, not just useful.
_PREFERRED_MIN_USEFUL = 2

_SECONDS_PER_DAY = 86400.0


@dataclass(slots=True)
class OutcomeScore:
    """The derived standing of one entry or one file."""

    useful: int = 0
    dead_end: int = 0
    corrected: int = 0
    useful_weight: float = 0.0
    negative_weight: float = 0.0
    score: float = 0.0
    status: str = "faded"
    last_ts: float = 0.0
    last_note: str = ""
    corrected_by: int | None = None

    def summary(self) -> dict:
        """Compact form attached to search and list hits."""
        out: dict = {
            "status": self.status,
            "score": round(self.score, 3),
            "useful": self.useful,
            "dead_end": self.dead_end,
            "corrected": self.corrected,
            "last_ts": self.last_ts,
        }
        if self.corrected_by:
            out["corrected_by"] = self.corrected_by
        if self.last_note:
            out["last_note"] = self.last_note[:200]
        return out


def decay_weight(age_seconds: float, half_life_days: float) -> float:
    """Weight of a verdict this old. A half-life of zero or less turns decay
    off (every verdict weighs 1); a timestamp in the future counts as now."""
    if half_life_days <= 0:
        return 1.0
    age_days = max(0.0, age_seconds) / _SECONDS_PER_DAY
    return 0.5 ** (age_days / half_life_days)


def score_outcomes(
    rows: Iterable[tuple[str, float, str, int | None]],
    now: float,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> OutcomeScore:
    """Fold (verdict, ts, note, corrected_by) rows for ONE target into a score.

    Rules, in order:
      1. a correction with no useful verdict after it: "corrected";
      2. total decayed weight under _MIN_WEIGHT: "faded";
      3. useful share of the weight at least _CLEAR_SHARE: "preferred" with
         at least _PREFERRED_MIN_USEFUL useful verdicts, else "useful";
      4. useful share at most 1 - _CLEAR_SHARE: "dead_end";
      5. otherwise "contested".
    The score is the useful weight minus the dead_end and corrected weight.
    """
    out = OutcomeScore()
    last_corrected_ts: float | None = None
    last_useful_ts: float | None = None
    for verdict, ts, note, corrected_by in sorted(rows, key=lambda r: r[1] or 0.0):
        if verdict not in VERDICTS:
            continue
        ts = float(ts or 0.0)
        w = decay_weight(now - ts, half_life_days)
        if verdict == "useful":
            out.useful += 1
            out.useful_weight += w
            last_useful_ts = ts
        else:
            out.negative_weight += w
            if verdict == "dead_end":
                out.dead_end += 1
            else:
                out.corrected += 1
                last_corrected_ts = ts
                if corrected_by:
                    out.corrected_by = int(corrected_by)
        if ts >= out.last_ts:
            out.last_ts = ts
            out.last_note = note or ""
    out.score = out.useful_weight - out.negative_weight
    total = out.useful_weight + out.negative_weight
    if last_corrected_ts is not None and (
        last_useful_ts is None or last_useful_ts <= last_corrected_ts
    ):
        out.status = "corrected"
    elif total < _MIN_WEIGHT:
        out.status = "faded"
    else:
        share = out.useful_weight / total
        if share >= _CLEAR_SHARE:
            out.status = (
                "preferred" if out.useful >= _PREFERRED_MIN_USEFUL else "useful"
            )
        elif share <= 1 - _CLEAR_SHARE:
            out.status = "dead_end"
        else:
            out.status = "contested"
    return out


def rank_factor(score: OutcomeScore | None) -> float:
    """Multiplier on a search relevance score: between 0.5 and 1.5 by the
    signed outcome score, 0.25 for a corrected entry, 1 with no outcome."""
    if score is None or score.status == "faded":
        return 1.0
    if score.status == "corrected":
        return 0.25
    return 1.0 + 0.5 * math.tanh(score.score)


def list_bucket(score: OutcomeScore | None) -> int:
    """Ordering tier for knowledge_list: useful entries first, then the
    unrated and contested ones, then dead ends, corrected entries last. Inside
    a tier the list stays newest first."""
    if score is None:
        return 1
    return {"preferred": 0, "useful": 0, "dead_end": 2, "corrected": 3}.get(
        score.status, 1
    )
