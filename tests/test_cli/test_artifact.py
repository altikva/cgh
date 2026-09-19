# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the `cgh artifact` read-through cache: note records a
#              note-kind, artifact-tagged knowledge entry carrying the file's
#              SHA-256; recall reports fresh vs stale against the file on disk;
#              list surfaces only artifact notes; and the Read precheck hook
#              surfaces a saved summary for an opaque file. Plus a guard that the
#              installed rule and bundled skill carry the artifact convention.

from __future__ import annotations

import argparse
import io
import json

import pytest


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.chdir(tmp_path)
    import codegraph.state.call_log as cl

    cl.reset_for_tests()
    yield tmp_path
    cl.reset_for_tests()


def _ns(artifact_cmd, path="", summary="", limit=30, root="."):
    return argparse.Namespace(
        artifact_cmd=artifact_cmd, path=path, summary=summary, limit=limit, root=root
    )


def _make_artifact(tmp_path, name="design.pdf", data=b"%PDF-1.4 fake pdf bytes"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_note_records_a_note_tagged_artifact(_fresh):
    from codegraph.cli.commands_artifact import cmd_artifact
    from codegraph.state.call_log import knowledge_list

    _make_artifact(_fresh)
    cmd_artifact(
        _ns("note", path="design.pdf", summary="Q3 revenue chart: 1.2M EUR, up 18%")
    )

    rows = knowledge_list(kind="note", tag="artifact")
    assert rows, "artifact summary was not recorded"
    top = rows[0]
    assert top["kind"] == "note"
    assert "artifact" in (top["tags"] or "")
    assert "design.pdf" in (top["file_refs"] or [])
    assert "Q3 revenue chart" in top["body"]
    # the content hash is embedded so recall can judge freshness
    from codegraph.cli.commands_artifact import parse_stored_sha

    assert parse_stored_sha(top["body"]) is not None


def test_note_without_summary_exits(_fresh):
    from codegraph.cli.commands_artifact import cmd_artifact

    _make_artifact(_fresh)
    with pytest.raises(SystemExit):
        cmd_artifact(_ns("note", path="design.pdf", summary="   "))


def test_note_on_missing_file_exits(_fresh):
    from codegraph.cli.commands_artifact import cmd_artifact

    with pytest.raises(SystemExit):
        cmd_artifact(_ns("note", path="nope.pdf", summary="whatever"))


def test_recall_reports_fresh_then_stale(_fresh):
    from codegraph.cli.commands_artifact import (
        _find_note,
        _freshness,
        _relref,
        cmd_artifact,
        parse_stored_sha,
    )

    p = _make_artifact(_fresh)
    cmd_artifact(_ns("note", path="design.pdf", summary="a chart"))

    ref = _relref(p, _fresh)
    hit = _find_note(ref, p, _fresh)
    assert hit is not None
    stored = parse_stored_sha(hit["body"])
    assert _freshness(p, stored) == "fresh"

    # change the file: the stored summary now describes an older version
    p.write_bytes(b"%PDF-1.4 different bytes now")
    assert _freshness(p, stored) == "stale"

    # missing file is reported, not crashed on
    p.unlink()
    assert _freshness(p, stored) == "missing"

    # the recall command runs cleanly across all three states
    cmd_artifact(_ns("recall", path="design.pdf"))


def test_list_surfaces_only_artifacts(_fresh):
    from codegraph.cli.commands_artifact import cmd_artifact
    from codegraph.state.call_log import knowledge_list, knowledge_record

    _make_artifact(_fresh, name="a.png", data=b"\x89PNG fake")
    cmd_artifact(_ns("note", path="a.png", summary="a screenshot of the login form"))
    # a plain note that is NOT an artifact must not be listed as one
    knowledge_record("some note", "not an artifact", kind="note", tags="misc")

    arts = knowledge_list(kind="note", tag="artifact")
    assert len(arts) == 1
    assert "a.png" in (arts[0]["file_refs"] or [])

    # list + recall paths run without error
    cmd_artifact(_ns("list"))


def test_read_hook_surfaces_saved_summary(_fresh, monkeypatch, capsys):
    from codegraph.cli.commands_artifact import cmd_artifact
    from codegraph.cli.commands_hooks import cmd_hook_precheck_read

    # repo_root discovery keys on .codegraph/fts.db existing
    (_fresh / ".codegraph" / "fts.db").write_bytes(b"")
    p = _make_artifact(_fresh, name="report.pdf", data=b"%PDF report body")
    cmd_artifact(
        _ns("note", path="report.pdf", summary="the 2026 headcount plan, 42 hires")
    )

    capsys.readouterr()  # discard the note command's stdout before the hook runs

    payload = {"tool_input": {"file_path": str(p)}, "cwd": str(_fresh)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        cmd_hook_precheck_read(argparse.Namespace())
    assert exc.value.code == 0

    out = json.loads(capsys.readouterr().out)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "report.pdf" in ctx
    assert "42 hires" in ctx  # the saved summary is surfaced
    assert "unchanged" in ctx  # fresh: the file matches the stored hash


def test_artifact_is_wired_into_the_installed_rule_and_skill():
    # The always-loaded rule cgh init writes must carry the artifact reflex,
    # and the bundled skill must be present for auto-install.
    from codegraph.integrations.skill_installer import (
        _USAGE_BODY,
        list_bundled_skills,
    )

    assert "artifact" in _USAGE_BODY.lower()
    assert 'tags="artifact"' in _USAGE_BODY  # the record path
    assert "cgh-artifacts" in list_bundled_skills()
