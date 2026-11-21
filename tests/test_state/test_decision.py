# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The pure ranking behind `decide`: which hits are candidates, the
#              supporting / against / untrusted split, the tier order, the rule
#              that a low match never heads the list, rivals, every row of the
#              confidence table, and the byte fitting of an answer.

from __future__ import annotations

import pytest

from cgh.state import decision as d


def hit(
    ident: int | str = 1,
    kind="decision",
    match="high",
    status=None,
    tags=(),
    **extra,
) -> dict:
    out = {
        "id": ident,
        "kind": kind,
        "title": f"entry {ident}",
        "body": f"body of entry {ident}",
        "tags": list(tags),
        "ts": 1_700_000_000.0,
        "confidence": match,
    }
    if status:
        out["outcome"] = {"status": status}
    out.update(extra)
    return out


# --- candidates and split -------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(d.KEPT_KINDS))
def test_kinds_that_decide_are_candidates(kind):
    assert d.is_candidate(hit(kind=kind))


@pytest.mark.parametrize("kind", ["note", "glossary", "artifact"])
def test_kinds_that_describe_are_dropped(kind):
    assert not d.is_candidate(hit(kind=kind))


def test_a_session_digest_is_dropped_whatever_its_kind():
    assert not d.is_candidate(hit(kind="decision", tags=["release", "session-digest"]))


def test_split_puts_false_trails_against_and_shared_hits_untrusted():
    hits = [
        hit(1),
        hit(2, status="dead_end"),
        hit(3, status="corrected"),
        hit("org:4", untrusted=True),
        hit(5, kind="note"),
    ]
    supporting, against, untrusted = d.split(hits)
    assert [h["id"] for h in supporting] == [1]
    assert [h["id"] for h in against] == [2, 3]
    assert [h["id"] for h in untrusted] == ["org:4"]


def test_a_shared_hit_this_repo_also_holds_counts_as_local():
    supporting, _against, untrusted = d.split(
        [hit("org:4", untrusted=True, local_copy=True)]
    )
    assert [h["id"] for h in supporting] == ["org:4"]
    assert untrusted == []


# --- ranking --------------------------------------------------------------------


def test_tiers_order_standing_preferred_useful_unrated_contested():
    hits = [
        hit(1, status="contested"),
        hit(2),
        hit(3, status="faded"),
        hit(4, status="useful"),
        hit(5, status="preferred"),
        hit(6, kind="standing_instruction"),
    ]
    assert [h["id"] for h in d.rank(hits)] == [6, 5, 4, 2, 3, 1]


def test_inside_a_tier_the_search_order_is_kept():
    hits = [hit(7), hit(3), hit(5)]
    assert [h["id"] for h in d.rank(hits)] == [7, 3, 5]


def test_a_low_match_never_heads_the_list_whatever_its_standing():
    hits = [hit(1, kind="standing_instruction", match="low"), hit(2, match="medium")]
    assert [h["id"] for h in d.rank(hits)] == [2, 1]


def test_rival_is_same_tier_and_same_match_as_the_head():
    ranked = d.rank(
        [
            hit(1, status="useful"),
            hit(2, status="useful"),
            hit(3, status="useful", match="medium"),
            hit(4),
        ]
    )
    assert [h["id"] for h in d.rivals(ranked)] == [2]


# --- confidence -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("hits", "expected"),
    [
        ([hit(1, kind="standing_instruction")], "high"),
        ([hit(1, status="preferred")], "high"),
        ([hit(1, status="useful")], "high"),
        ([hit(1, status="useful"), hit(2, status="useful")], "medium"),
        ([hit(1)], "medium"),
        ([hit(1, status="faded")], "medium"),
        ([hit(1), hit(2)], "medium"),
        ([hit(1, match="medium", status="useful")], "medium"),
        ([hit(1, match="medium")], "low"),
        ([hit(1, match="medium", kind="standing_instruction")], "low"),
        ([hit(1, status="contested")], "low"),
        ([hit(1, match="low", status="preferred")], "low"),
    ],
)
def test_confidence_table(hits, expected):
    level, basis = d.confidence(d.rank(hits), [], [])
    assert level == expected
    assert basis.startswith(f"match {hits[0]['confidence']}")


def test_only_refuted_or_shared_evidence_is_low():
    level, basis = d.confidence([], [hit(1, status="dead_end")], [hit("o:2")])
    assert level == "low"
    assert "1 refuted" in basis and "1 from a shared store" in basis


def test_no_candidate_is_none():
    assert d.confidence([], [], []) == ("none", "no precedent found")


# --- the answer -----------------------------------------------------------------


def test_answer_recommends_the_head_and_quotes_it():
    answer = d.build_answer("q", [hit(1), hit(2, status="useful")])
    rec = answer["recommendation"]
    assert rec["entry_id"] == 2
    assert rec["statement"] == "body of entry 2"
    assert rec["recorded"] == "2023-11-14T22:13Z"
    assert rec["outcome"] == {"status": "useful"}
    assert answer["confidence"] == "high"
    assert "knowledge_outcome(entry_id=2" in answer["next"]
    assert "suggestion" not in answer and "contested" not in answer
    supporting = answer["evidence"]["supporting"]
    assert [e["id"] for e in supporting] == [2, 1]
    # The recommended entry is quoted once, as the statement.
    assert "excerpt" not in supporting[0] and "excerpt" in supporting[1]


def test_answer_with_a_rival_is_contested_and_lists_it():
    answer = d.build_answer("q", [hit(1), hit(2)])
    assert answer["contested"] is True
    assert [a["entry_id"] for a in answer["alternatives"]] == [2]
    assert "statement" not in answer["alternatives"][0]
    assert "1 rival of equal standing" in answer["basis"]


def test_answer_below_medium_recommends_nothing_and_suggests_a_record():
    suggestion = {"tool": "knowledge_record", "kind": "decision", "tags": "x"}
    answer = d.build_answer("q", [hit(1, match="medium")], suggestion=suggestion)
    assert answer["recommendation"] is None
    assert answer["confidence"] == "low"
    assert answer["suggestion"] == suggestion
    assert "next" not in answer
    # What was found is still reported as evidence.
    assert [e["id"] for e in answer["evidence"]["supporting"]] == [1]


def test_a_shared_hit_is_never_recommended_nor_raises_confidence():
    shared = hit("org:9", untrusted=True, namespace="org", origin_repo="media")
    answer = d.build_answer("q", [shared])
    assert answer["recommendation"] is None
    assert answer["confidence"] == "low"
    assert answer["evidence"]["untrusted"] == [
        {
            "id": "org:9",
            "title": "entry org:9",
            "kind": "decision",
            "match": "high",
            "excerpt": "body of entry org:9",
            "namespace": "org",
            "origin_repo": "media",
        }
    ]
    with_local = d.build_answer("q", [hit(1), shared])
    assert with_local["recommendation"]["entry_id"] == 1
    assert with_local["confidence"] == "medium"


def test_a_dead_end_is_reported_against_with_its_note_and_fix():
    refuted = hit(1, status="corrected")
    refuted["outcome"].update(last_note="broke the release", corrected_by=7)
    answer = d.build_answer("q", [refuted])
    assert answer["recommendation"] is None
    (against,) = answer["evidence"]["against"]
    assert against["outcome"] == {
        "status": "corrected",
        "last_note": "broke the release",
    }
    assert against["corrected_by"] == 7


def test_limit_caps_each_evidence_list():
    hits = [hit(i) for i in range(1, 6)] + [
        hit(i, status="dead_end") for i in range(6, 11)
    ]
    answer = d.build_answer("q", hits, limit=2)
    assert len(answer["evidence"]["supporting"]) == 2
    assert len(answer["evidence"]["against"]) == 2


# --- fitting --------------------------------------------------------------------


def _big_answer() -> dict:
    long_body = "word " * 400
    hits = [hit(1, status="useful", body=long_body)]
    hits += [hit(i, body=long_body) for i in range(2, 6)]
    hits += [hit(i, status="dead_end", body=long_body) for i in range(6, 9)]
    hits += [hit(f"o:{i}", untrusted=True, body=long_body) for i in range(9, 12)]
    return d.build_answer("q", hits)


def test_fit_leaves_a_small_answer_alone():
    answer = d.build_answer("q", [hit(1), hit(2, status="useful")])
    before = {k: (list(v) if isinstance(v, list) else v) for k, v in answer.items()}
    d.fit_answer(answer, 10_000)
    assert answer["evidence"]["supporting"][1]["excerpt"] == "body of entry 1"
    assert "dropped" not in answer
    assert set(answer) == set(before)


def test_fit_drops_evidence_from_the_tail_before_touching_the_statement():
    answer = _big_answer()
    statement = answer["recommendation"]["statement"]
    size = d._json_bytes(answer)
    d.fit_answer(answer, size - 300)
    # Excerpts went, starting with the shared hits; the statement is intact.
    assert "excerpt" not in answer["evidence"]["untrusted"][-1]
    assert answer["recommendation"]["statement"] == statement
    assert "dropped" not in answer


def test_fit_keeps_the_recommendation_and_one_refuted_entry_at_any_budget():
    answer = _big_answer()
    d.fit_answer(answer, 400)
    evidence = answer["evidence"]
    assert evidence["untrusted"] == []
    assert len(evidence["supporting"]) == 1
    assert len(evidence["against"]) == 1
    assert answer["dropped"] == 3 + 4 + 2
    statement = answer["recommendation"]["statement"]
    assert 150 <= len(statement) <= 220
