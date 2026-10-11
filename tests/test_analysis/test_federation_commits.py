# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Federated answers name the commit each scope is read at: the
#              checked-out HEAD and branch, the commit its index was built at,
#              and whether they agree. Shown in the `scopes` map of federated
#              MCP responses and in the commit column of `cgh federate list`.

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import federation
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server.tools_query import register as register_query

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/usr/local/bin"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
        env=_ENV,
    ).stdout.strip()


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _repo(path: Path, branch: str, body: str) -> None:
    path.mkdir(parents=True)
    (path / "mod.py").write_text(body)
    (path / ".gitignore").write_text(".codegraph/\n")
    _git(path, "init", "-q", "-b", branch)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")


@pytest.fixture
def fed(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    federation.reset_for_tests()
    reset_connection()
    base = tmp_path.resolve()
    parent, child = base / "parent", base / "parent" / "api"
    _repo(parent, "main", "def front():\n    return 1\n")
    _repo(child, "develop", "def needle_fn():\n    return 2\n")
    (parent / ".gitignore").write_text(".codegraph/\napi/\n")
    index_repo(child, method="os_walk")
    reset_connection()
    (parent / ".codegraph").mkdir(exist_ok=True)
    (parent / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nsubrepos = ["./api"]\nfederate_auto_up = false\n'
    )
    index_repo(parent, method="os_walk")
    _srv._root = parent
    m = _FakeMcp()
    register_query(m)
    yield parent, child, m.tools
    reset_connection()
    _srv._root = None
    federation.reset_for_tests()


def test_federated_answer_names_each_scope_commit(fed):
    _parent, child, t = fed
    out = json.loads(t["pattern_search"]("needle_fn"))
    assert {h["scope"] for h in out["hits"]} == {"api"}
    api = out["scopes"]["api"]
    assert api["head"] == _git(child, "rev-parse", "HEAD")
    assert api["branch"] == "develop"
    assert api["indexed_commit"] == api["head"] and api["index_at_head"] is True
    assert out["scopes"]["parent"]["branch"] == "main"


def test_index_behind_head_is_visible(fed):
    _parent, child, t = fed
    (child / "mod.py").write_text("def needle_fn():\n    return 3\n")
    _git(child, "commit", "-qam", "move on")
    federation.reset_for_tests()  # past the few-second cache
    out = json.loads(t["symbol_lookup"]("needle_fn"))
    api = out["scopes"]["api"]
    assert api["index_at_head"] is False
    assert api["indexed_commit"] != api["head"]


def test_no_scopes_without_federation(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    root = tmp_path.resolve()
    (root / "mod.py").write_text("def solo():\n    return 1\n")
    index_repo(root, method="os_walk")
    _srv._root = root
    m = _FakeMcp()
    register_query(m)
    try:
        out = json.loads(m.tools["symbol_lookup"]("solo"))
        assert "scopes" not in out
    finally:
        reset_connection()
        _srv._root = None


def test_federate_list_shows_the_commit(fed, capsys):
    parent, child, _t = fed
    from codegraph.cli import commands_federate as cf

    cf.console.width = 200
    cf._render_status_table(parent, [child])
    text = capsys.readouterr().out
    assert _git(child, "rev-parse", "HEAD")[:8] in text
    assert "develop" in text
