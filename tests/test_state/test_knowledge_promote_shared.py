# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Native knowledge promotion into a shared fleet store (L9.3):
#              promote_knowledge re-targeted at ~/.cgh/shared/<ns>, provenance
#              carried (repo, ticket, author, symptom, owner), entry/since/tags
#              selection, dry-run, idempotence, the standing_instruction/glossary
#              refusal, cross-repo supersede isolation, and the CLI verb.

from __future__ import annotations

import json
import time
from argparse import Namespace
from pathlib import Path

import pytest

from cgh.state.call_log import (
    _get_conn_at,
    knowledge_record,
    promote_knowledge,
    repo_owner_id,
    reset_for_tests,
    shared_store_path,
)

_COLS = (
    "id, title, body, kind, namespace, owner, origin_repo, ticket, author, "
    "symptom, origin_id, superseded_by, source_branch"
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect GLOBAL_DIR so shared stores land under the test's tmp dir."""
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    yield tmp_path
    reset_for_tests()


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / ".cgh").mkdir(parents=True)
    return root


def _rows(ns: str) -> list:
    conn = _get_conn_at(shared_store_path(ns))
    return conn.execute(f"SELECT {_COLS} FROM knowledge ORDER BY id").fetchall()


def _titles(ns: str) -> list[str]:
    return [r[1] for r in _rows(ns)]


# ---------------------------------------------------------------------------
# Promotion into a shared store + provenance
# ---------------------------------------------------------------------------


def test_promote_carries_entry_and_provenance_to_the_shared_store(home):
    repo = _repo(home)
    knowledge_record(
        "fleet decision", "handlers return an envelope", kind="decision",
        tags="api", repo_root=repo, ticket="media#412", author="media-manager",
        symptom="docker is not installed",
    )  # fmt: skip
    res = promote_knowledge(
        repo, to_namespace="org-platform", source_branch="feature/x"
    )
    assert res["promoted"] == 1 and res["copied"] == 1
    assert res["target_namespace"] == "org-platform"

    rows = _rows("org-platform")
    assert len(rows) == 1
    (_id, title, _body, kind, ns, owner, origin_repo, ticket, author, symptom,
     origin_id, superseded_by, branch) = rows[0]  # fmt: skip
    assert title == "fleet decision" and kind == "decision"
    assert ns == "org-platform"
    assert owner == repo_owner_id(repo)  # owner derived server-side
    assert origin_repo == "repo"  # the source repo's slug, carried as provenance
    assert ticket == "media#412"
    assert author == "media-manager"
    assert symptom == "docker is not installed"
    assert origin_id is not None
    assert branch == "feature/x"
    assert superseded_by is None


def test_dry_run_writes_nothing(home):
    repo = _repo(home)
    knowledge_record("d", "b", kind="decision", repo_root=repo)
    res = promote_knowledge(repo, to_namespace="org-platform", dry_run=True)
    assert res["copied"] == 1 and res["dry_run"] is True
    assert _rows("org-platform") == []


def test_promote_to_shared_is_idempotent(home):
    repo = _repo(home)
    knowledge_record("d", "b", kind="decision", repo_root=repo)
    first = promote_knowledge(repo, to_namespace="org-platform")
    second = promote_knowledge(repo, to_namespace="org-platform")
    assert first["promoted"] == 1
    assert second["promoted"] == 0 and second["skipped_duplicate"] == 1
    assert len(_rows("org-platform")) == 1  # no duplicate row


# ---------------------------------------------------------------------------
# Selection: only_ids, tags_any, since_ts
# ---------------------------------------------------------------------------


def test_only_ids_restricts_to_named_entries(home):
    repo = _repo(home)
    a = knowledge_record("a", "x", kind="decision", repo_root=repo)
    knowledge_record("b", "y", kind="decision", repo_root=repo)
    promote_knowledge(repo, to_namespace="org-platform", only_ids=[a])
    assert _titles("org-platform") == ["a"]


def test_tags_any_filters_by_tag(home):
    repo = _repo(home)
    knowledge_record("tagged", "x", kind="decision", tags="infra", repo_root=repo)
    knowledge_record("other", "y", kind="decision", tags="ui", repo_root=repo)
    promote_knowledge(repo, to_namespace="org-platform", tags_any=["infra"])
    assert _titles("org-platform") == ["tagged"]


def test_since_filters_by_time(home):
    repo = _repo(home)
    knowledge_record("before", "x", kind="decision", repo_root=repo)
    mid = time.time()
    time.sleep(0.02)
    knowledge_record("after", "y", kind="decision", repo_root=repo)
    promote_knowledge(repo, to_namespace="org-platform", since_ts=mid)
    assert _titles("org-platform") == ["after"]


# ---------------------------------------------------------------------------
# Guards respected at the shared write
# ---------------------------------------------------------------------------


def test_standing_instruction_and_glossary_are_refused_in_a_shared_store(home):
    repo = _repo(home)
    knowledge_record("rule", "always X", kind="standing_instruction", repo_root=repo)
    knowledge_record("term", "RFM = ...", kind="glossary", repo_root=repo)
    knowledge_record("decision", "body", kind="decision", repo_root=repo)
    res = promote_knowledge(repo, to_namespace="org-platform")
    assert res["refused_shared_kind"] == 2
    assert res["promoted"] == 1
    assert _titles("org-platform") == ["decision"]


def test_bad_namespace_is_rejected(home):
    repo = _repo(home)
    knowledge_record("d", "b", kind="decision", repo_root=repo)
    with pytest.raises(ValueError):
        promote_knowledge(repo, to_namespace="../../etc")
    with pytest.raises(ValueError):
        promote_knowledge(repo, to_namespace="repo")  # not a shared namespace


def test_missing_target_raises(home):
    repo = _repo(home)
    with pytest.raises(ValueError):
        promote_knowledge(repo)  # neither to_root nor to_namespace


# ---------------------------------------------------------------------------
# Supersede: own rows retire, other repos' rows are untouchable
# ---------------------------------------------------------------------------


def test_revised_entry_supersedes_its_own_shared_copy(home):
    repo = _repo(home)
    first = knowledge_record("conv", "v1", kind="decision", repo_root=repo)
    promote_knowledge(repo, to_namespace="org-platform", source_branch="f/a")
    knowledge_record("conv2", "v2", kind="decision", repo_root=repo, supersedes=first)
    res = promote_knowledge(repo, to_namespace="org-platform", source_branch="f/a")
    assert res["superseded"] == 1 and res["copied"] == 0
    live = [r for r in _rows("org-platform") if r[11] is None]
    assert [r[2] for r in live] == ["v2"]  # only the revision is live


def test_dedup_does_not_touch_another_repos_shared_row(home):
    a = _repo(home, "repo-a")
    b = _repo(home, "repo-b")
    knowledge_record("shared", "vA", kind="decision", repo_root=a)
    promote_knowledge(a, to_namespace="org-platform")
    # repo-b promotes the same content: it is a duplicate, not a new row.
    knowledge_record("shared", "vA", kind="decision", repo_root=b)
    res = promote_knowledge(b, to_namespace="org-platform")
    assert res["promoted"] == 0 and res["skipped_duplicate"] == 1
    rows = _rows("org-platform")
    assert len(rows) == 1
    assert rows[0][5] == repo_owner_id(a)  # still owned by repo-a
    assert rows[0][11] is None  # and still live


def test_a_repo_cannot_supersede_another_repos_shared_row(home):
    ra = _repo(home, "ra")
    rb = _repo(home, "rb")
    knowledge_record("x", "v1", kind="decision", repo_root=ra)
    promote_knowledge(ra, to_namespace="org-platform")
    # repo-b revises an entry whose first version matches repo-a's shared row.
    v1b = knowledge_record("x", "v1", kind="decision", repo_root=rb)
    knowledge_record("x2", "v2", kind="decision", repo_root=rb, supersedes=v1b)
    promote_knowledge(rb, to_namespace="org-platform")
    by_title = {r[1]: r for r in _rows("org-platform")}
    # repo-a's row is NOT retired by repo-b's promotion; repo-b's v2 is added.
    assert by_title["x"][11] is None
    assert by_title["x"][5] == repo_owner_id(ra)
    assert "x2" in by_title


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------


def _promote_args(src: Path, **kw) -> Namespace:
    base = dict(
        knowledge_cmd="promote", from_=str(src), to="", since="", tags="",
        kinds="", no_notes=False, pr="", branch="", commit="", session="",
        archive="", dry_run=False, json=True,
    )  # fmt: skip
    base.update(kw)
    return Namespace(**base)


def test_cli_promote_defaults_to_the_configured_namespace(home, capsys):
    from cgh.cli.commands_knowledge import cmd_knowledge

    repo = _repo(home)
    knowledge_record("d", "b", kind="decision", repo_root=repo)
    cmd_knowledge(_promote_args(repo))  # no --to -> config default
    out = json.loads(capsys.readouterr().out)
    assert out["promoted"] == 1
    assert out["to_namespace"] == "org-platform"
    assert _titles("org-platform") == ["d"]


def test_cli_promote_to_named_namespace_with_tags(home, capsys):
    from cgh.cli.commands_knowledge import cmd_knowledge

    repo = _repo(home)
    knowledge_record("keep", "b", kind="decision", tags="infra", repo_root=repo)
    knowledge_record("drop", "c", kind="decision", tags="ui", repo_root=repo)
    cmd_knowledge(_promote_args(repo, to="akonteo", tags="infra"))
    out = json.loads(capsys.readouterr().out)
    assert out["to_namespace"] == "akonteo" and out["promoted"] == 1
    assert _titles("akonteo") == ["keep"]
