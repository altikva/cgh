# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Traversal precision on both backends. Calls through an
#              interface method (Protocol, ABC) with a single implementation
#              are followed into it by find_callees, impact_of and
#              path_between, several implementations stop the walk; the
#              max_tokens budget of subgraph and impact_of trims the farthest
#              rows; the lookup verbs exit 3 for an unknown name and callees
#              4 for an ambiguous one; an inline code span naming a symbol
#              is an exact doc reference.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo

REPO = {
    "app/__init__.py": "",
    "app/ports.py": (
        "import abc\n"
        "from typing import Protocol\n\n\n"
        "class Sink(Protocol):\n"
        "    def push(self, x): ...\n\n\n"
        "class Store(Protocol):\n"
        "    def put(self, x): ...\n\n\n"
        "class Cleaner(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def scrub(self, x): ...\n"
    ),
    "app/impl.py": (
        "from app.util import serialize\n\n\n"
        "class DiskSink:\n"
        "    def push(self, x):\n        return serialize(x)\n\n\n"
        "class MemStore:\n"
        "    def put(self, x):\n        return x\n\n\n"
        "class FileStore:\n"
        "    def put(self, x):\n        return x\n"
    ),
    "app/clean.py": (
        "from app.ports import Cleaner\n\n\n"
        "class SoapCleaner(Cleaner):\n"
        "    def scrub(self, x):\n        return x\n"
    ),
    "app/util.py": "def serialize(x):\n    return x\n",
    "app/job.py": (
        "from app.ports import Cleaner, Sink, Store\n\n\n"
        "class Job:\n"
        "    def __init__(self, sink: Sink, store: Store, cleaner: Cleaner):\n"
        "        self._sink = sink\n"
        "        self._store = store\n"
        "        self._cleaner = cleaner\n\n"
        "    def go(self):\n        return self._sink.push(1)\n\n"
        "    def save(self):\n        return self._store.put(1)\n\n"
        "    def wash(self):\n        return self._cleaner.scrub(1)\n"
    ),
    "app/dup_a.py": "def twin():\n    return 1\n",
    "app/dup_b.py": "def twin():\n    return 2\n",
    "docs/guide.md": (
        "# Guide\n\n"
        "Call `serialize` to turn a value into bytes.\n"
        "The `MemStore` keeps it in memory, see `FileStore.put(x)` too.\n"
        "Nothing is called `nonexistent_thing`.\n"
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


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_docs import register as register_docs
    from codegraph.server.tools_insight import register as register_insight
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_insight(m)
    register_docs(m)
    return m.tools


def _label(node: str) -> str:
    return node.rsplit("::", 1)[-1]


# --- interface dispatch -----------------------------------------------------


def test_callees_follow_a_single_implementation(repo):
    out = json.loads(_tools()["find_callees"]("Job.go", max_depth=2))
    rows = [(c["callee"], c["depth"], c["resolution"]) for c in out["callees"]]
    assert ("push", 1, "interface") in rows
    impl = next(c for c in out["callees"] if c["resolution"] == "interface")
    assert impl["file"].endswith("app/impl.py")
    assert impl["via"] == "Sink.push"
    # The walk goes on from the implementation.
    assert ("serialize", 2, "resolved") in rows


def test_callees_follow_an_abc_implementation(repo):
    out = json.loads(_tools()["find_callees"]("Job.wash"))
    impl = [c for c in out["callees"] if c["resolution"] == "interface"]
    assert [c["file"].rsplit("/", 1)[-1] for c in impl] == ["clean.py"]


def test_callees_stop_at_several_implementations(repo):
    out = json.loads(_tools()["find_callees"]("Job.save", max_depth=2))
    assert all(c["resolution"] != "interface" for c in out["callees"])
    put = next(c for c in out["callees"] if c["callee"] == "put")
    assert put["implementations"] == 2


def test_path_between_crosses_a_single_implementation(repo):
    out = json.loads(_tools()["path_between"]("go", "serialize"))
    assert out["found"] is True
    assert [_label(n) for n in out["path"]] == [
        "Job.go",
        "Sink.push",
        "DiskSink.push",
        "serialize",
    ]
    assert [h["resolution"] for h in out["hops"]][1] == "interface"
    assert json.loads(_tools()["path_between"]("save", "put"))["found"] is True
    # Two implementations of Store.put: no path into either one's body.
    rows = json.loads(_tools()["find_callees"]("Job.save"))["callees"]
    assert {c["callee"] for c in rows} == {"put"}


def test_impact_reaches_interface_callers_of_a_single_implementation(repo):
    out = json.loads(_tools()["impact_of"]("serialize"))
    by_node = {_label(r["node"]): r["resolution"] for r in out["impacted"]}
    assert by_node["DiskSink.push"] == "resolved"
    assert by_node["Job.go"] == "interface"


# --- token budget -----------------------------------------------------------


def test_impact_budget_trims_the_farthest(repo):
    tools = _tools()
    full = json.loads(tools["impact_of"]("serialize"))
    assert "dropped" not in full
    small = json.loads(tools["impact_of"]("serialize", max_tokens=150))
    assert small["truncated"] is True
    assert small["dropped"] >= 1
    assert small["count"] == len(small["impacted"])
    kept = [r["node"] for r in small["impacted"]]
    assert kept == [r["node"] for r in full["impacted"]][: len(kept)]


def test_subgraph_budget(repo):
    tools = _tools()
    target = str(repo / "app/ports.py")
    full = json.loads(tools["subgraph"](target))
    assert "truncated" not in full and len(full["depended_by"]) >= 2
    small = json.loads(tools["subgraph"](target, max_tokens=60))
    assert small["truncated"] is True
    assert small["dropped"] >= 1
    assert len(json.dumps(small, indent=2)) <= 60 * 4 + 200
    deep = json.loads(tools["subgraph"](str(repo / "app/job.py"), 2, max_tokens=10))
    assert deep["truncated"] is True and deep["reachable"] == []


def test_fit_tokens_without_budget_is_a_no_op():
    from codegraph.server._budget import fit_tokens

    payload = {"rows": list(range(100))}
    assert fit_tokens(payload, ["rows"], 0) == 0
    assert payload == {"rows": list(range(100))}


# --- CLI exit codes ---------------------------------------------------------


@pytest.fixture
def cli(repo, monkeypatch):
    from codegraph.cli import commands_query

    monkeypatch.setattr(commands_query, "_ask_owner", lambda *a, **k: None)
    return commands_query


def _code(fn, **kwargs) -> int:
    try:
        fn(argparse.Namespace(**kwargs))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_lookup_exit_codes(repo, cli):
    assert _code(cli.cmd_lookup, root=str(repo), name="serialize") == 0
    assert _code(cli.cmd_lookup, root=str(repo), name="twin") == 0
    assert _code(cli.cmd_lookup, root=str(repo), name="no_such_name") == 3


def test_callers_exit_codes(repo, cli):
    assert _code(cli.cmd_callers, root=str(repo), fn_name="serialize") == 0
    # Defined, called by nobody: an answer, not an error.
    assert _code(cli.cmd_callers, root=str(repo), fn_name="twin") == 0
    assert _code(cli.cmd_callers, root=str(repo), fn_name="no_such_name") == 3
    assert _code(cli.cmd_callers, root=str(repo), fn_name="Job.go") == 0


def test_callees_exit_codes(repo, cli, capsys):
    assert _code(cli.cmd_callees, root=str(repo), fn_name="no_such_name") == 3
    capsys.readouterr()
    assert _code(cli.cmd_callees, root=str(repo), fn_name="twin") == 4
    err = capsys.readouterr().err
    assert "dup_a.py:1" in err and "dup_b.py:1" in err
    assert _code(cli.cmd_callees, root=str(repo), fn_name="dup_a.twin") == 0
    assert _code(cli.cmd_callees, root=str(repo), fn_name="DiskSink.push") == 0
    out = capsys.readouterr().out
    assert "serialize" in out
    # The local path follows a single implementation like find_callees.
    assert _code(cli.cmd_callees, root=str(repo), fn_name="Job.go") == 0
    assert "impl.py" in capsys.readouterr().out


# --- exact doc mentions -----------------------------------------------------


def test_exact_mention_parsing():
    from codegraph.parsers.markdown import _exact_mention

    assert _exact_mention("serialize") == "serialize"
    assert _exact_mention(" run_all() ") == "run_all"
    assert _exact_mention("get") == ""  # a name every library uses
    assert _exact_mention("ab") == ""
    assert _exact_mention("FileStore.put(x)") == ""
    assert _exact_mention("x = 1") == ""


def test_doc_refs_mark_exact_and_heuristic(repo):
    tools = _tools()
    refs = json.loads(tools["doc_refs"]("serialize"))["doc_references"]
    linked = [r for r in refs if r["ref_type"] == "function"]
    assert [(r["match"], r["context"]) for r in linked] == [("exact", "inline")]
    refs = json.loads(tools["doc_refs"]("MemStore"))["doc_references"]
    assert [r["match"] for r in refs if r["ref_type"] == "class"] == ["exact"]
    refs = json.loads(tools["doc_refs"]("nonexistent_thing"))["doc_references"]
    assert all(r["ref_type"] == "text_mention" for r in refs)
    assert all(r["match"] == "heuristic" for r in refs)
