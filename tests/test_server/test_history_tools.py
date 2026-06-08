# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the git-history MCP tools (hotspots, who_knows).
#              Builds a tiny indexed repo that is also a git repo with a
#              pinned author identity, registers the tools on a fake mcp, and
#              asserts the JSON each tool returns joins churn with the import
#              graph (hotspots) and rolls up the author (who_knows).

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

import codegraph.server as _srv
from codegraph.analysis import churn
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_history import register as register_history

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


class _FakeMcp:
    """Minimal stand-in for FastMCP: .tool() records the decorated function
    unchanged so tests can call the tool bodies directly."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Alice",
            "-c",
            "user.email=alice@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def history_repo(tmp_path):
    """Indexed + git-tracked repo. lib.py is imported by app.py (so lib has
    import in-degree 1) and is committed twice (so it churns more)."""
    reset_connection()
    churn.clear_cache()
    _srv._root = tmp_path.resolve()
    _srv._conn = None

    _git(tmp_path, "init", "-q")

    lib = tmp_path / "lib.py"
    app = tmp_path / "app.py"
    lib.write_text("def helper():\n    return 1\n", encoding="utf-8")
    app.write_text("import lib\n\ndef run():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", "lib.py", "app.py")
    _git(tmp_path, "commit", "-q", "-m", "first")

    lib.write_text("def helper():\n    return 2\n", encoding="utf-8")
    _git(tmp_path, "add", "lib.py")
    _git(tmp_path, "commit", "-q", "-m", "second")

    index_file(lib, tmp_path.resolve())
    index_file(app, tmp_path.resolve())

    yield tmp_path.resolve()

    reset_connection()
    churn.clear_cache()
    _srv._root = None
    _srv._conn = None


def _tools():
    m = _FakeMcp()
    register_history(m)
    return m.tools


def test_hotspots_ranks_high_churn_central_file(history_repo):
    tools = _tools()
    out = json.loads(tools["hotspots"]())

    files = {h["file"]: h for h in out["hotspots"]}
    assert "lib.py" in files
    # lib.py: 2 commits and imported by app.py.
    assert files["lib.py"]["commits"] == 2
    assert files["lib.py"]["importers"] == 1
    # lib.py should outscore app.py (more churn + an importer).
    assert "app.py" in files
    assert files["lib.py"]["score"] >= files["app.py"]["score"]
    assert "note" in out


def test_hotspots_includes_authors(history_repo):
    tools = _tools()
    out = json.loads(tools["hotspots"]())
    lib = next(h for h in out["hotspots"] if h["file"] == "lib.py")
    names = {a["name"] for a in lib["authors"]}
    assert "Alice" in names


def test_who_knows_returns_author(history_repo):
    tools = _tools()
    out = json.loads(tools["who_knows"]("lib.py"))
    assert out["file"] == "lib.py"
    names = {a["name"] for a in out["authors"]}
    assert names == {"Alice"}
    assert out["authors"][0]["commits"] == 2


def test_who_knows_accepts_absolute_path(history_repo):
    tools = _tools()
    out = json.loads(tools["who_knows"](str(history_repo / "app.py")))
    names = {a["name"] for a in out["authors"]}
    assert names == {"Alice"}


def test_who_knows_unknown_file_has_note(history_repo):
    tools = _tools()
    out = json.loads(tools["who_knows"]("does_not_exist.py"))
    assert out["authors"] == []
    assert "no git history" in out["note"]
