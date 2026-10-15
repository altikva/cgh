# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search's answer budget: lines cut short, the hit list
#              trimmed to a byte budget, and the hits left out counted per file.

from __future__ import annotations

import json

from codegraph.analysis.pattern import SHOWN_LINE_LEN, fit_to_budget
from codegraph.server.tools_query import _hits_payload


def _hits(n: int, text: str = "x" * 150, files: int = 3) -> list[dict]:
    return [
        {"scope": "parent", "file": f"/r/f{i % files}.py", "line": i, "text": text}
        for i in range(1, n + 1)
    ]


def test_small_list_is_kept_whole():
    hits = _hits(5)
    kept, more = fit_to_budget(hits)
    assert len(kept) == 5 and more == {}


def test_lines_are_stripped_and_cut():
    kept, _ = fit_to_budget(_hits(1, text="    " + "y" * 500))
    assert kept[0]["text"] == "y" * SHOWN_LINE_LEN


def test_long_list_is_cut_to_the_budget_with_the_rest_counted():
    hits = _hits(200)
    kept, more = fit_to_budget(hits, budget=4_000)
    assert len(json.dumps(kept)) <= 4_000 + 300
    assert len(kept) + sum(more.values()) == 200
    # The cut keeps the order: what is shown is the head of the list.
    assert [h["line"] for h in kept] == list(range(1, len(kept) + 1))


def test_first_hit_is_kept_even_over_budget():
    kept, more = fit_to_budget(_hits(3), budget=10)
    assert len(kept) == 1 and sum(more.values()) == 2


def test_payload_says_what_was_left_out():
    out = _hits_payload(_hits(200))
    assert out["total"] == 200
    assert "more" in out and "Narrow the pattern" in out["note"]
    assert "more" not in _hits_payload(_hits(3))
