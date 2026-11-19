# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Symptom (error-signature) search (L9.4): the normaliser gommes
#              volatile fragments while keeping signal tokens so a look-alike
#              error recalls a diagnosis, the repo search carries each hit's
#              verdict for the confirmed / dead_end split, and the read-only
#              shared-store search reads another store's signatures and outcomes.

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cgh.state.call_log import (
    _get_conn_at,
    knowledge_outcome_record,
    knowledge_record,
    knowledge_symptom_search,
    knowledge_symptom_search_ro,
    normalize_symptom,
    reset_for_tests,
    shared_store_path,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    yield tmp_path
    reset_for_tests()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_normalize_gommes_paths_numbers_hex_and_timestamps():
    line = (
        "2026-11-14T08:12:03Z ERROR pid 48213 at "
        "/Users/joy/app/db.py:120 conn 0xDEADBEEF failed"
    )
    norm = normalize_symptom(line)
    assert "<ts>" in norm and "<path>" in norm and "<hex>" in norm and "<n>" in norm
    assert "/Users/joy" not in norm
    assert "48213" not in norm


def test_look_alike_lines_share_one_signature():
    # Same error, different run: volatile path, pid and timestamp differ.
    a = "2026-11-14T08:12:03Z sqlite3.OperationalError: database is locked (pid 123)"
    b = "2026-11-17T22:51:40Z sqlite3.OperationalError: database is locked (pid 99999)"
    assert normalize_symptom(a) == normalize_symptom(b)
    # The signal survives normalisation.
    assert "database is locked" in normalize_symptom(a)
    assert "OperationalError" in normalize_symptom(a)


def test_quoted_values_gommed_but_reason_codes_and_keywords_kept():
    norm = normalize_symptom("ModuleNotFoundError: No module named 'requests'")
    assert "<q>" in norm and "requests" not in norm
    assert "ModuleNotFoundError" in norm
    # An ALL_CAPS reason code survives even quoted.
    reason = normalize_symptom('google.api_core: reason "SERVICE_DISABLED" returned')
    assert "SERVICE_DISABLED" in reason
    # A known diagnostic phrase survives quoting too.
    kept = normalize_symptom('docker: "is not installed" on this host')
    assert "is not installed" in kept


def test_epoch_timestamp_reads_as_ts_not_number():
    assert "<ts>" in normalize_symptom("expired at 1731830400")
    assert "<ts>" in normalize_symptom("expired at 1731830400123")


def test_empty_symptom_is_empty():
    assert normalize_symptom("") == ""


# ---------------------------------------------------------------------------
# Repo search + verdicts
# ---------------------------------------------------------------------------


def test_resembling_error_recalls_the_diagnosis(home):
    root = _repo(home)
    knowledge_record(
        title="DB lock under a live owner",
        body="close the RO conn before opening RW",
        kind="gotcha",
        symptom="sqlite3.OperationalError: database is locked (pid 123)",
        repo_root=root,
    )
    hits = knowledge_symptom_search(
        "2026-11-17T22:51:40Z sqlite3.OperationalError: database is locked (pid 99999)",
        repo_root=root,
    )
    assert [h["title"] for h in hits] == ["DB lock under a live owner"]
    assert hits[0]["match"] == "symptom"
    assert hits[0]["score"] == 1.0  # look-alike normalises to the same signature


def test_verdict_rides_each_hit_for_the_dead_end_split(home):
    root = _repo(home)
    good = knowledge_record(
        title="good fix", body="this worked", kind="gotcha",
        symptom="boom: SERVICE_DISABLED on project foo", repo_root=root,
    )  # fmt: skip
    bad = knowledge_record(
        title="false trail", body="restarting does nothing", kind="gotcha",
        symptom="boom: SERVICE_DISABLED on project bar", repo_root=root,
    )  # fmt: skip
    knowledge_outcome_record("useful", entry_id=good, repo_root=root)
    knowledge_outcome_record("dead_end", entry_id=bad, repo_root=root)
    hits = {
        h["title"]: h
        for h in knowledge_symptom_search(
            "boom: SERVICE_DISABLED on project baz", repo_root=root
        )
    }
    assert hits["good fix"]["verdict"] == "useful"
    assert hits["false trail"]["verdict"] == "dead_end"


def test_unrelated_error_does_not_match(home):
    root = _repo(home)
    knowledge_record(
        title="db lock", body="x", kind="gotcha",
        symptom="sqlite3.OperationalError: database is locked", repo_root=root,
    )  # fmt: skip
    hits = knowledge_symptom_search(
        "ValueError: invalid literal for int() with base 10", repo_root=root
    )
    assert hits == []


# ---------------------------------------------------------------------------
# Read-only shared store
# ---------------------------------------------------------------------------


def test_shared_store_symptom_search_ro_reads_signatures_and_verdicts(home):
    root = _repo(home)
    entry = knowledge_record(
        title="fleet diagnosis",
        body="echo of the preflight, docker is fine",
        kind="gotcha",
        symptom="docker is not installed",
        namespace="org-platform",
        repo_root=root,
    )
    # Mark it a dead end in the shared store itself (the outcome lives in the
    # same DB as the entry it rates).
    shared = _get_conn_at(shared_store_path("org-platform"))
    shared.execute(
        "INSERT INTO knowledge_outcome(entry_id, verdict, note, file_refs, "
        "corrected_by, session_id, ts) VALUES (?, 'dead_end', '', '', NULL, '', ?)",
        (entry, time.time()),
    )
    shared.commit()
    reset_for_tests()  # drop the cached RW conn so the RO open is clean
    hits = knowledge_symptom_search_ro(
        shared_store_path("org-platform"), "docker is not installed on CI"
    )
    assert [h["title"] for h in hits] == ["fleet diagnosis"]
    assert hits[0]["verdict"] == "dead_end"


def test_shared_store_symptom_search_ro_absent_store_is_empty(home):
    assert knowledge_symptom_search_ro(shared_store_path("org-platform"), "x") == []
