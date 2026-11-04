# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Every caller / callee row of the call tools says how its edge
#              was obtained: resolved when the call's shape fixes the
#              target, name for a match on the callee's name with one
#              definition, ambiguous with several, interface through an
#              implemented interface method. Checked on both backends,
#              through callers_of, find_callees, impact_of, path_between and
#              the CLI markers.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis.callers import callers_of
from codegraph.analysis.provenance import EdgeProvenance, best
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

REPO = {
    "app/__init__.py": "",
    "app/util.py": (
        "def helper():\n    return 1\n\n\ndef local_caller():\n    return helper()\n"
    ),
    "app/a.py": (
        "from app.util import helper\n\n\ndef imported_caller():\n    return helper()\n"
    ),
    "app/models.py": (
        "from typing import Protocol\n\n\n"
        "class Repo:\n"
        "    def fetch(self):\n        return 1\n\n"
        "    def persist(self):\n        return 2\n\n"
        "    def run(self):\n        return self.fetch()\n\n\n"
        "class Cache:\n"
        "    def fetch(self):\n        return 3\n\n\n"
        "class Sink(Protocol):\n"
        "    def push(self, x): ...\n\n\n"
        "class DiskSink:\n"
        "    def push(self, x):\n        return x\n"
    ),
    "app/b.py": (
        "from app.models import Repo, Sink\n\n\n"
        "def typed_caller():\n    return Repo().fetch()\n\n\n"
        "def unknown_caller(obj):\n    return obj.fetch()\n\n\n"
        "def unknown_single(obj):\n    return obj.persist()\n\n\n"
        "class Job:\n"
        "    def __init__(self, sink: Sink):\n        self._sink = sink\n\n"
        "    def go(self):\n        return self._sink.push(1)\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    root = tmp_path.resolve() / "repo"
    _write(root, REPO)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _by_caller(root: Path, name: str) -> dict[str, str]:
    return {
        r["caller"]: r["resolution"] for r in callers_of(get_connection(root), name)
    }


def test_shape_fixed_calls_are_resolved(repo):
    assert _by_caller(repo, "helper") == {
        "local_caller": "resolved",  # same file
        "imported_caller": "resolved",  # the import names the file
    }


def test_self_and_typed_receivers_are_resolved_unknown_ones_are_not(repo):
    rows = _by_caller(repo, "fetch")
    assert rows["run"] == "resolved"  # self.fetch() in Repo
    assert rows["typed_caller"] == "resolved"  # Repo().fetch()
    # obj.fetch(): two methods of that name in a file the caller imports.
    assert rows["unknown_caller"] == "ambiguous"
    # obj.persist(): one definition, still a guess on the name.
    assert _by_caller(repo, "persist") == {"unknown_single": "name"}


def test_interface_rows(repo):
    rows = _by_caller(repo, "DiskSink.push")
    assert rows == {"go": "interface"}


def test_edge_provenance_without_given_targets(repo):
    conn = get_connection(repo)
    prov = EdgeProvenance(conn)
    unknown = str(repo / "app/b.py") + "::unknown_caller"
    cache = str(repo / "app/models.py") + "::Cache.fetch"
    assert prov.of(unknown, cache) == "ambiguous"
    typed = str(repo / "app/b.py") + "::typed_caller"
    assert prov.of(typed, str(repo / "app/models.py") + "::Repo.fetch") == "resolved"


def test_best_keeps_the_most_certain():
    assert best(["ambiguous", "resolved"]) == "resolved"
    assert best(["ambiguous", "name"]) == "name"
    assert best([]) == "name"


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_insight import register as register_insight
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_insight(m)
    return m.tools


def test_tools_carry_resolution(repo):
    tools = _tools()
    callers = json.loads(tools["find_callers"]("persist"))["callers"]
    assert [c["resolution"] for c in callers] == ["name"]

    callees = json.loads(tools["find_callees"]("unknown_caller"))["callees"]
    assert {c["resolution"] for c in callees} == {"ambiguous"}
    callees = json.loads(tools["find_callees"]("run"))["callees"]
    assert [(c["callee"], c["resolution"]) for c in callees] == [("fetch", "resolved")]

    impact = json.loads(tools["impact_of"]("DiskSink.push"))
    by_node = {
        r["node"].rsplit("::", 1)[-1]: r["resolution"] for r in impact["impacted"]
    }
    assert by_node["Job.go"] == "interface"
    impact = json.loads(tools["impact_of"]("helper"))
    assert {r["resolution"] for r in impact["impacted"]} == {"resolved"}

    path = json.loads(tools["path_between"]("unknown_single", "persist"))
    assert path["found"] is True
    assert [h["resolution"] for h in path["hops"]] == ["name"]
    imports = json.loads(
        tools["path_between"](
            str(repo / "app/a.py"), str(repo / "app/util.py"), edge="IMPORTS"
        )
    )
    assert "hops" not in imports


def test_cli_marks_guessed_rows(repo, capsys, monkeypatch):
    import argparse

    from codegraph.cli import commands_query

    monkeypatch.setattr(commands_query, "_ask_owner", lambda *a, **k: None)
    commands_query.cmd_callers(argparse.Namespace(root=str(repo), fn_name="fetch"))
    out = capsys.readouterr().out
    lines = {ln.strip(" │├└─") for ln in out.splitlines()}
    assert any(ln.startswith("unknown_caller") and ln.endswith("??") for ln in lines)
    assert any(ln.startswith("run") and "?" not in ln for ln in lines)
    assert "matched by name only" in out

    commands_query.cmd_callees(
        argparse.Namespace(root=str(repo), fn_name="unknown_single")
    )
    out = capsys.readouterr().out
    assert any(ln.strip(" │├└─").endswith("?") for ln in out.splitlines())
