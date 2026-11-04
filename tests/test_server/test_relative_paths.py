# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tool answers carry repo-relative paths and the root to rebuild
#              the absolute one: parent rows against the project root, rows of
#              a federated child against that child's root, paths under no
#              root left absolute, and the CLI still served absolute paths.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.cli.hook_answers import _rel
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.server._paths import relative_answer, relative_result

PARENT = Path("/work/project")
CHILD = Path("/work/project/services/api")
SIBLING = Path("/work/sibling")


def _children() -> dict[str, Path]:
    return {"api": CHILD, "sibling": SIBLING}


def test_parent_rows_become_relative_with_one_root():
    out = relative_answer(
        {
            "definitions": [
                {"file": "/work/project/pkg/a.py", "lines": "1-4"},
                {"file": "/work/project/pkg/b.py", "lines": "5-9"},
            ]
        },
        PARENT,
        _children,
    )
    assert out is not None
    assert [d["file"] for d in out["definitions"]] == ["pkg/a.py", "pkg/b.py"]
    assert out["root"] == str(PARENT)
    assert "roots" not in out
    assert (Path(out["root"]) / out["definitions"][0]["file"]).as_posix() == (
        "/work/project/pkg/a.py"
    )


def test_child_rows_are_relative_to_their_own_root():
    out = relative_answer(
        {
            "callers": [
                {"scope": "parent", "file": "/work/project/main.py"},
                {"scope": "api", "file": "/work/project/services/api/app.py"},
                {"scope": "sibling", "file": "/work/sibling/lib/x.py"},
            ]
        },
        PARENT,
        _children,
    )
    assert out is not None
    files = [c["file"] for c in out["callers"]]
    assert files == ["main.py", "app.py", "lib/x.py"]
    assert out["root"] == str(PARENT)
    assert out["roots"] == {"api": str(CHILD), "sibling": str(SIBLING)}


def test_children_are_only_resolved_when_a_row_names_one():
    def boom() -> dict[str, Path]:
        raise AssertionError("children looked up for a parent-only answer")

    out = relative_answer({"file": "/work/project/a.py"}, PARENT, boom)
    assert out == {"file": "a.py", "root": str(PARENT)}


def test_paths_outside_every_root_stay_absolute():
    out = relative_answer(
        {
            "hits": [
                {"file": "/elsewhere/extra/x.py"},
                {"scope": "unknown", "file": "/work/project/y.py"},
                {"file": "/work/projectile/z.py"},
            ]
        },
        PARENT,
        _children,
    )
    assert out is None


def test_line_suffixes_lists_and_keys_are_rewritten():
    out = relative_answer(
        {
            "targets": ["/work/project/a.py", "/work/project/b.py"],
            "at": "/work/project/a.py:12",
            "by_file": {"/work/project/a.py": 3},
            "note": "see /work/project/a.py",
        },
        PARENT,
        _children,
    )
    assert out is not None
    assert out["targets"] == ["a.py", "b.py"]
    assert out["at"] == "a.py:12"
    assert out["by_file"] == {"a.py": 3}
    # Free text is left as written: a path inside a sentence may be a command.
    assert out["note"] == "see /work/project/a.py"


def test_an_existing_root_field_is_kept():
    out = relative_answer(
        {"root": "/work/project", "file": "/work/project/a.py"}, PARENT, _children
    )
    assert out == {"file": "a.py", "root": "/work/project"}


def test_the_root_itself_is_not_emptied():
    assert relative_answer({"path": "/work/project"}, PARENT, _children) is None


def test_non_object_results_are_left_alone():
    assert relative_result("plain /work/project/a.py", PARENT) == (
        "plain /work/project/a.py"
    )
    listing = json.dumps(["/work/project/a.py"])
    assert relative_result(listing, PARENT) == listing
    raw = json.dumps({"file": "src/a.py"})
    assert relative_result(raw, PARENT) is raw


def test_hook_rel_keeps_an_already_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _rel("pkg/a.py", Path("/work/project")) == "pkg/a.py"
    assert _rel(str(tmp_path / "pkg" / "a.py"), tmp_path) == "pkg/a.py"


# ---------------------------------------------------------------------------
# Through the tool wrapper
# ---------------------------------------------------------------------------


def _wrapped(monkeypatch, origin: str, root: Path):
    monkeypatch.setattr(_srv, "_call_origin", lambda: origin)
    monkeypatch.setattr(_srv, "_root", root)
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    @_srv._logged_tool
    def symbol_lookup_fake(name: str) -> str:
        return json.dumps({"definitions": [{"file": f"{root}/pkg/{name}.py"}]})

    return symbol_lookup_fake


@pytest.mark.parametrize("origin", ["agent", "hook"])
def test_agent_and_hook_answers_are_relative(monkeypatch, tmp_path, origin):
    out = json.loads(_wrapped(monkeypatch, origin, tmp_path)(name="a"))
    assert out["definitions"][0]["file"] == "pkg/a.py"
    assert out["root"] == str(tmp_path)


def test_cli_answers_keep_absolute_paths(monkeypatch, tmp_path):
    out = json.loads(_wrapped(monkeypatch, "cli", tmp_path)(name="a"))
    assert out["definitions"][0]["file"] == f"{tmp_path}/pkg/a.py"
    assert "root" not in out


# ---------------------------------------------------------------------------
# Real tools on an indexed repo
# ---------------------------------------------------------------------------


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    reset_connection()
    root = tmp_path.resolve()
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def target_fn():\n    return 1\n")
    (pkg / "main.py").write_text(
        "from pkg.core import target_fn\n\n\ndef run_it():\n    return target_fn()\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from pkg.core import target_fn\n\n\ndef test_it():\n    assert target_fn()\n"
    )
    index_repo(root, method="os_walk")
    monkeypatch.setattr(_srv, "_root", root)
    monkeypatch.setattr(_srv, "_conn", None)
    monkeypatch.setattr(_srv, "_fts_conn", None)
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")
    monkeypatch.setattr("codegraph.state.call_log.log_call", lambda **k: None)

    from codegraph.server import _register_insight, _register_query, _register_tests

    m = _FakeMcp()
    for register in (_register_insight, _register_query, _register_tests):
        register(m)
    yield root, m.tools
    reset_connection()


_CALLS = [
    ("symbol_lookup", {"name": "target_fn"}),
    ("find_callers", {"fn_name": "target_fn"}),
    ("find_callees", {"fn_name": "run_it"}),
    ("pattern_search", {"pattern": "target_fn"}),
    ("file_summary", {"file_path": "pkg/core.py"}),
    ("tests_for", {"symbol_or_file": "pkg/core.py"}),
    ("impact_of", {"symbol_or_file": "target_fn"}),
]


@pytest.mark.parametrize(("tool", "args"), _CALLS, ids=[c[0] for c in _CALLS])
def test_tool_answers_carry_no_absolute_repo_path(tools, tool, args):
    root, registered = tools
    raw = registered[tool](**args)
    assert str(root) + "/" not in raw
    out = json.loads(raw)
    assert out["root"] == str(root)


def test_a_relative_answer_still_leads_to_the_file(tools):
    _root, registered = tools
    out = json.loads(registered["symbol_lookup"](name="target_fn"))
    first = out["definitions"][0]
    assert first["file"] == "pkg/core.py"
    assert (Path(out["root"]) / first["file"]).is_file()
