# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search's answer budget: hits grouped by file, lines
#              clipped around the match, the hit list trimmed to a byte
#              budget, and the hits left out counted per file; a
#              sentence's own fields shrunk to their share of the answer.

from __future__ import annotations

import json

from codegraph.analysis.pattern import (
    MORE_FILES_SHOWN,
    PATTERN_ANSWER_BYTES,
    SHOWN_LINE_LEN,
    clip_line,
    group_hits,
    match_finder,
)
from codegraph.server._budget import answer_bytes
from codegraph.server.tools_query import _fit_extra, _hits_payload


def _hits(
    n: int, text: str = "x" * 150, files: int = 3, scope: str = "parent"
) -> list[dict]:
    return [
        {"scope": scope, "file": f"/r/f{i % files}.py", "line": i, "text": text}
        for i in range(1, n + 1)
    ]


def test_small_list_is_kept_whole_and_grouped_by_file():
    groups, shown, more = group_hits(_hits(6))
    assert shown == 6 and more == {}
    assert [g["file"] for g in groups] == ["/r/f1.py", "/r/f2.py", "/r/f0.py"]
    assert groups[0]["hits"] == [[1, "x" * 150], [4, "x" * 150]]
    assert "scope" not in groups[0]


def test_scope_is_kept_for_a_federated_search():
    groups, _, _ = group_hits(_hits(2, scope="api"), with_scope=True)
    assert groups[0]["scope"] == "api"


def test_line_without_match_is_cut_at_its_head():
    groups, _, _ = group_hits(_hits(1, text="    " + "y" * 500))
    assert groups[0]["hits"][0][1] == "y" * SHOWN_LINE_LEN + "…"


def test_line_is_clipped_around_the_match():
    line = "a" * 300 + "NEEDLE" + "b" * 300
    out = clip_line(line, match_finder("needle", regex=False)(line))
    assert "NEEDLE" in out
    assert out.startswith("…") and out.endswith("…")
    assert len(out) <= SHOWN_LINE_LEN + 2


def test_match_at_line_start_has_no_leading_ellipsis():
    line = "NEEDLE" + "b" * 300
    out = clip_line(line, match_finder("NEEDLE")(line))
    assert out.startswith("NEEDLE") and out.endswith("…")


def test_regex_python_cannot_compile_falls_back_to_literal():
    assert match_finder("(?<name", regex=True)("x (?<name y") == (2, 9)


def test_long_list_is_cut_to_the_budget_with_the_rest_counted():
    groups, shown, more = group_hits(_hits(200), budget=4_000)
    assert len(json.dumps(groups).encode()) <= 4_000 + 300
    assert shown + sum(more.values()) == 200
    # The cut keeps the order: what is shown is the head of the list.
    lines = sorted(h[0] for g in groups for h in g["hits"])
    assert lines == list(range(1, shown + 1))


def test_first_hit_is_kept_even_over_budget():
    _, shown, more = group_hits(_hits(3), budget=10)
    assert shown == 1 and sum(more.values()) == 2


def test_payload_says_what_was_left_out():
    out = _hits_payload(_hits(200), "x")
    assert out["total"] == 200 and out["truncated"] is True
    assert out["shown"] + sum(out["more"].values()) == 200
    assert "Narrow the pattern" in out["hint"]
    small = _hits_payload(_hits(3))
    assert "more" not in small and "truncated" not in small


def test_oversized_answer_stays_near_the_budget():
    """Many long lines over many files: the whole payload stays bounded, and
    `more` lists only the first files."""
    hits = [
        {
            "scope": "parent",
            "file": f"/repo/src/pkg/module_{i}.py",
            "line": i,
            "text": "z" * 390,
        }
        for i in range(400)
    ]
    out = _hits_payload(hits, "z")
    size = len(json.dumps(out, ensure_ascii=False).encode())
    assert size <= PATTERN_ANSWER_BYTES
    assert out["truncated"] is True
    assert len(out["more"]) == MORE_FILES_SHOWN
    assert out["more_files"] > MORE_FILES_SHOWN


def test_cut_answer_keeps_the_narrowing_fields():
    """At about 2.5 KB a broad search still names where its hits are and
    how to narrow."""
    hits = [
        {"scope": "parent", "file": f"/repo/{d}/m{i}.py", "line": i, "text": "q" * 120}
        for d in ("api", "web")
        for i in range(60)
    ]
    out = _hits_payload(hits, "q", roots=["/repo"])
    assert answer_bytes(out) + 300 <= PATTERN_ANSWER_BYTES
    assert out["top_dirs"] and out["more"]
    assert "count_only=True" in out["hint"] and "glob" in out["hint"]


def _sentence_extra(n: int) -> dict:
    return {
        "interpreted_as": ["webhooks", "retry"],
        "expanded": ["hook", "callback", "retries", "backoff", "rejouer", "relancer"],
        "symbols": [
            {
                "kind": "function",
                "name": f"symbol_{i}",
                "file": f"/repo/pkg/module_{i}.py",
                "lines": f"{i}-{i + 9}",
                "doc": "d" * 100,
            }
            for i in range(n)
        ],
        "docs": [
            {
                "kind": "md_section",
                "name": f"s{i}",
                "file": "/repo/README.md",
                "lines": "1-9",
            }
            for i in range(3)
        ],
        "confidence": "medium",
    }


def test_sentence_fields_lose_docstrings_before_symbols():
    extra = _sentence_extra(6)
    # Measured as received: the escaped quotes of six symbols weigh ~1.15 KB.
    _fit_extra(extra, 1_200)
    assert answer_bytes(extra) <= 1_200
    assert len(extra["symbols"]) == 6
    assert "doc" not in extra["symbols"][-1]
    assert "symbols_more" not in extra


def test_sentence_fields_cut_symbols_with_a_count():
    extra = _sentence_extra(8)
    _fit_extra(extra, 700)
    assert answer_bytes(extra) <= 700
    assert len(extra["symbols"]) >= 3
    assert extra["symbols_more"] == 8 - len(extra["symbols"])
    assert extra["confidence"] == "medium"


def test_small_sentence_fields_are_left_alone():
    extra = _sentence_extra(2)
    before = json.dumps(extra)
    _fit_extra(extra, 2_000)
    assert json.dumps(extra) == before
