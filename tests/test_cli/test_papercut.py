# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the `cgh papercut` verb: it records a papercut as a
#              gotcha-kind knowledge entry tagged "papercut", requires a
#              symptom, and its list/search path only surfaces papercut-tagged
#              entries.

from __future__ import annotations

import argparse

import pytest


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.chdir(tmp_path)
    import codegraph.state.call_log as cl

    cl.reset_for_tests()
    yield tmp_path
    cl.reset_for_tests()


def _ns(words, fix="", project="", limit=15, root="."):
    return argparse.Namespace(
        words=words, fix=fix, project=project, limit=limit, root=root
    )


def test_add_records_a_papercut_tagged_gotcha(_fresh):
    from codegraph.cli.commands_papercut import cmd_papercut
    from codegraph.state.call_log import knowledge_search

    cmd_papercut(
        _ns(["add", "gcloud", "token", "stale"], fix="gcloud auth login --update-adc")
    )
    hits = knowledge_search("papercut")
    assert hits, "papercut was not recorded"
    top = hits[0]
    assert "papercut" in (top["tags"] or "")
    assert top["kind"] == "gotcha"
    assert "gcloud token stale" in top["title"]
    assert "update-adc" in top["body"]
    assert "Symptom:" in top["body"] and "Fix:" in top["body"]


def test_add_without_a_symptom_exits(_fresh):
    from codegraph.cli.commands_papercut import cmd_papercut

    with pytest.raises(SystemExit):
        cmd_papercut(_ns(["add"]))


def test_search_surfaces_only_papercuts(_fresh):
    from codegraph.cli.commands_papercut import cmd_papercut
    from codegraph.state.call_log import knowledge_record, knowledge_search

    cmd_papercut(_ns(["add", "duckdb lock wedged"], fix="cgh serve --stop"))
    # a plain gotcha that is NOT a papercut must not be counted as one
    knowledge_record(
        "A routing gotcha", "not a papercut", kind="gotcha", tags="routing"
    )

    papercuts = [
        r
        for r in knowledge_search("papercut", limit=50)
        if "papercut" in (r.get("tags") or "")
    ]
    assert len(papercuts) == 1
    assert "duckdb lock wedged" in papercuts[0]["title"]

    # list and query paths run without error
    cmd_papercut(_ns([]))
    cmd_papercut(_ns(["duckdb"]))
