# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI query commands against a real DuckDB index. Pins the v0.5
#              regression where cmd_search / cmd_lookup / cmd_callers /
#              cmd_callees / cmd_outline sent raw Cypher to the DuckDB
#              backend and crashed with a ParserException as soon as no
#              owner held the lock. Also covers the federated fan-out:
#              a subrepo's symbols show up with its scope tag.

from __future__ import annotations

import argparse
import io
import json
import subprocess

import pytest
from rich.console import Console

import codegraph.cli.commands_query as cq
from codegraph.analysis.federation import ScopedResult, add_subrepo
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


def _mk_indexed_repo(root, py_body: str, md_body: str | None = None):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    (root / "lib.py").write_text(py_body, encoding="utf-8")
    if md_body is not None:
        (root / "README.md").write_text(md_body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    reset_connection()
    index_repo(str(root))
    reset_connection()
    return root


@pytest.fixture
def indexed_repo(tmp_path):
    yield _mk_indexed_repo(
        tmp_path / "repo",
        "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n",
        "# Guide\n\n## Usage\n\nSome text.\n",
    )
    reset_connection()


@pytest.fixture
def captured_console(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cq, "console", Console(file=buf, width=200, no_color=True))
    return buf


class TestQueryOnDuckDB:
    def test_search_json_returns_symbols(self, indexed_repo, capsys):
        cq.cmd_search(
            argparse.Namespace(
                root=str(indexed_repo), query="helper", limit=10, offset=0, json=True
            )
        )
        out = json.loads(capsys.readouterr().out)
        names = {r["name"] for r in out["results"]}
        assert "helper" in names
        assert all(r["scope"] == "parent" for r in out["results"])

    def test_lookup_prints_definition(self, indexed_repo, captured_console):
        cq.cmd_lookup(argparse.Namespace(root=str(indexed_repo), name="helper"))
        assert "helper" in captured_console.getvalue()

    def test_callers(self, indexed_repo, captured_console):
        cq.cmd_callers(argparse.Namespace(root=str(indexed_repo), fn_name="helper"))
        assert "run" in captured_console.getvalue()

    def test_callees(self, indexed_repo, captured_console):
        cq.cmd_callees(argparse.Namespace(root=str(indexed_repo), fn_name="run"))
        assert "helper" in captured_console.getvalue()

    def test_outline(self, indexed_repo, captured_console):
        cq.cmd_outline(argparse.Namespace(root=str(indexed_repo), file="README.md"))
        assert "Usage" in captured_console.getvalue()


class TestFederatedQuery:
    def test_search_reaches_subrepo_with_scope_tag(self, tmp_path, capsys):
        parent = _mk_indexed_repo(
            tmp_path / "parent", "def parent_fn():\n    return 1\n"
        )
        child = _mk_indexed_repo(
            tmp_path / "childrepo", "def child_only_fn():\n    return 2\n"
        )
        add_subrepo(parent, child)
        reset_connection()

        cq.cmd_search(
            argparse.Namespace(
                root=str(parent), query="child_only_fn", limit=10, offset=0, json=True
            )
        )
        out = json.loads(capsys.readouterr().out)
        hits = [r for r in out["results"] if r["name"] == "child_only_fn"]
        assert hits
        assert hits[0]["scope"] == "childrepo"

    def test_page_is_not_monopolised_by_the_parent(self, tmp_path, capsys):
        """A parent that fills the page on its own must not starve children."""
        parent = _mk_indexed_repo(
            tmp_path / "parent",
            "".join(f"def shared_p{i}():\n    return {i}\n\n\n" for i in range(30)),
        )
        child = _mk_indexed_repo(
            tmp_path / "childrepo",
            "".join(f"def shared_c{i}():\n    return {i}\n\n\n" for i in range(30)),
        )
        add_subrepo(parent, child)
        reset_connection()

        cq.cmd_search(
            argparse.Namespace(
                root=str(parent), query="shared_", limit=10, offset=0, json=True
            )
        )
        out = json.loads(capsys.readouterr().out)
        scopes = {r["scope"] for r in out["results"]}
        assert len(out["results"]) == 10
        assert scopes == {"parent", "childrepo"}

    def test_locked_child_falls_back_to_fts(self, tmp_path, capsys, monkeypatch):
        """A child whose owner holds the graph lock still answers from FTS."""
        parent = _mk_indexed_repo(
            tmp_path / "parent", "def parent_fn():\n    return 1\n"
        )
        child = _mk_indexed_repo(
            tmp_path / "childrepo", "def child_only_fn():\n    return 2\n"
        )
        add_subrepo(parent, child)
        reset_connection()

        def _locked(root, fn):
            return [
                ScopedResult(
                    scope="childrepo",
                    scope_path=child,
                    payload=None,
                    error="db unavailable (missing or locked)",
                )
            ]

        monkeypatch.setattr(cq, "for_each_child_graphdb", _locked)

        cq.cmd_search(
            argparse.Namespace(
                root=str(parent), query="child_only_fn", limit=10, offset=0, json=True
            )
        )
        out = json.loads(capsys.readouterr().out)
        hits = [r for r in out["results"] if r["scope"] == "childrepo"]
        assert hits
        assert hits[0]["name"] == "child_only_fn"
        assert not out.get("warnings")

    def test_locked_child_lookup_falls_back_to_fts(
        self, tmp_path, captured_console, monkeypatch
    ):
        """A child whose owner holds the graph lock still resolves the name."""
        parent = _mk_indexed_repo(
            tmp_path / "parent", "def parent_fn():\n    return 1\n"
        )
        child = _mk_indexed_repo(
            tmp_path / "childrepo", "def child_only_fn():\n    return 2\n"
        )
        add_subrepo(parent, child)
        reset_connection()

        def _locked(root, fn):
            return [
                ScopedResult(
                    scope="childrepo",
                    scope_path=child,
                    payload=None,
                    error="db unavailable (missing or locked)",
                )
            ]

        monkeypatch.setattr(cq, "for_each_child_graphdb", _locked)

        cq.cmd_lookup(argparse.Namespace(root=str(parent), name="child_only_fn"))
        out = captured_console.getvalue()
        assert "child_only_fn" in out
        assert "childrepo" in out
        assert "unavailable" not in out

    def test_lookup_reaches_subrepo(self, tmp_path, captured_console):
        parent = _mk_indexed_repo(
            tmp_path / "parent", "def parent_fn():\n    return 1\n"
        )
        child = _mk_indexed_repo(
            tmp_path / "childrepo", "def child_only_fn():\n    return 2\n"
        )
        add_subrepo(parent, child)
        reset_connection()

        cq.cmd_lookup(argparse.Namespace(root=str(parent), name="child_only_fn"))
        out = captured_console.getvalue()
        assert "child_only_fn" in out
        assert "childrepo" in out
