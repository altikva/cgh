# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Query understanding: a sentence told apart from an identifier,
#              a regex or a line of code, the identifiers it names, the
#              synonym expansion, and the ranking and narrowing helpers.

from __future__ import annotations

import pytest

from codegraph.analysis.query_intent import (
    SYNONYMS,
    coverage,
    extract_identifiers,
    interpret,
    is_definition,
    is_test_path,
    looks_like_sentence,
    rank_hits,
    synonyms_of,
    top_dirs,
)


@pytest.mark.parametrize(
    "query",
    [
        "where are webhooks retried",
        "comment est calculée la taille d'une réponse",
        "how is the answer cut when it is too big",
        "who calls cap_answer()",
        "webhook retry?",
        "où est défini `RetryPolicy`",
    ],
)
def test_sentences_are_detected(query):
    assert looks_like_sentence(query)


@pytest.mark.parametrize(
    "query",
    [
        "cap_answer",
        "RetryPolicy",
        "class Association",
        r"@router\.(get|post)",
        "def .*retry",
        "from x import y",
        "self._root is None",
        "x is not None",
        "import json",
        "foo|bar",
        "TODO: fix",
        "colou?r",
        "",
    ],
)
def test_identifiers_regexes_and_code_are_not_sentences(query):
    assert not looks_like_sentence(query)


def test_identifiers_are_extracted_in_order():
    q = "is `max_results` passed to PatternHit or to codegraph.analysis.pattern and _root?"
    assert extract_identifiers(q) == [
        "max_results",
        "PatternHit",
        "codegraph.analysis.pattern",
        "_root",
    ]


def test_quoted_and_called_names_are_identifiers():
    assert extract_identifiers('who calls "spawn_owner" and dumps()') == [
        "spawn_owner",
        "dumps",
    ]
    assert extract_identifiers("what does 'resolve' return") == ["resolve"]


def test_abbreviations_are_not_dotted_names():
    assert extract_identifiers("e.g. the owner, i.e. the server") == []


def test_interpretation_keeps_meaningful_words_and_expands_synonyms():
    intent = interpret("where are webhooks retried")
    assert intent.sentence
    assert intent.interpreted_as == ["webhooks", "retried"]
    assert "retry" in intent.expanded or "backoff" in intent.expanded
    assert "relance" in intent.expanded  # French side of the concept


def test_french_words_expand_to_english_code_words():
    intent = interpret("comment est calculée la taille d'une réponse")
    assert intent.interpreted_as == ["calculée", "taille", "réponse"]
    for word in ("size", "response", "compute"):
        assert word in intent.expanded


def test_filler_words_are_dropped():
    intent = interpret("where is the function that handles the retry logic")
    assert intent.terms == ["retry"]


def test_not_a_sentence_is_left_alone():
    intent = interpret("cap_answer")
    assert not intent.sentence
    assert intent.interpreted_as == [] and intent.fts_text == ""


def test_synonym_table_is_data_and_symmetric():
    assert len(SYNONYMS) >= 40
    assert "retry" in synonyms_of("relancer")
    assert "relancer" in synonyms_of("retries")
    assert synonyms_of("zorglub") == []


def test_coverage_counts_ideas_through_synonyms():
    intent = interpret("comment est calculée la taille d'une réponse")
    assert coverage(intent, "answer_bytes", "Size in bytes of the answer") >= 2
    assert coverage(intent, "parse_csv", "Read a CSV table") == 0


def test_definition_lines():
    assert is_definition("def cap_answer(payload):")
    assert is_definition("    async def run(self):")
    assert is_definition("export function useThing() {")
    assert is_definition('resource "google_storage_bucket" "b" {')
    assert not is_definition("    cap_answer(payload, keys, hint)")
    assert not is_definition("from x import cap_answer")


def test_test_paths():
    assert is_test_path("/r/tests/test_x.py")
    assert is_test_path("src/thing.spec.ts")
    assert not is_test_path("/r/codegraph/server/_budget.py")


def test_definitions_rank_before_uses():
    hits = [
        {"file": "/r/a_uses.py", "line": 3, "text": "    cap_answer(x)"},
        {"file": "/r/a_uses.py", "line": 9, "text": "    cap_answer(y)"},
        {"file": "/r/tests/test_b.py", "line": 1, "text": "def cap_answer_test():"},
        {"file": "/r/z_budget.py", "line": 10, "text": "def cap_answer(payload):"},
    ]
    ranked = rank_hits(hits, {})
    assert ranked[0]["file"] == "/r/z_budget.py"
    # A test file holding a definition still comes before plain uses.
    assert [h["file"] for h in ranked[1:]] == [
        "/r/tests/test_b.py",
        "/r/a_uses.py",
        "/r/a_uses.py",
    ]
    # One file's hits keep their line order.
    assert [h["line"] for h in ranked[2:]] == [3, 9]


def test_more_imported_files_rank_first_among_uses():
    hits = [
        {"file": "/r/a.py", "line": 1, "text": "x = needle"},
        {"file": "/r/b.py", "line": 1, "text": "y = needle"},
    ]
    ranked = rank_hits(hits, {"/r/b.py": 12, "/r/a.py": 1})
    assert [h["file"] for h in ranked] == ["/r/b.py", "/r/a.py"]


def test_top_dirs_go_deep_enough_to_split_the_hits():
    files = ["/r/pkg/a/x.py"] * 12 + ["/r/pkg/b/y.py"] * 5 + ["/r/docs/z.md"] * 2
    from pathlib import Path

    dirs = top_dirs(files, [Path("/r")])
    assert dirs == [
        {"glob": "pkg/a/**", "hits": 12},
        {"glob": "pkg/b/**", "hits": 5},
        {"glob": "docs/*", "hits": 2},
    ]


def test_top_dirs_of_repo_relative_paths():
    dirs = top_dirs(["src/a.ts", "src/b.ts", "lib/c.ts", "README.md"])
    assert dirs[0] == {"glob": "src/**", "hits": 2}
