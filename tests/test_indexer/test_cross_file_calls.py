# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Cross-file CALLS edges must not depend on the order files are
#              indexed in, and must survive a reindex of the callee's file.
#              Both used to fail: a call into a file indexed later was never
#              linked, and saving the callee's file erased every caller in
#              other files until the next full index. Run on both backends.

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import incremental_reindex, index_file, index_repo
from codegraph.state.scan_meta import GRAPH_FORMAT, read_meta


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    yield request.param
    reset_connection()


LIB = "def helper():\n    return 1\n"
APP = "from lib import helper\n\n\ndef run():\n    return helper()\n"
TEST = "from lib import helper\n\n\ndef test_helper():\n    assert helper() == 1\n"
# Defines its own helper and calls it: the local definition wins.
LOCAL = "def helper():\n    return 2\n\n\ndef user():\n    return helper()\n"
# Calls helper through a star import: no import names it, so every
# definition of the star-imported files is a candidate.
STAR = "from lib import *\nfrom alt import *\n\n\ndef run():\n    return helper()\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _edges(root: Path) -> set[tuple[str, str]]:
    res = get_connection(root).execute("SELECT from_id, to_id FROM edge_calls")
    out = set()
    while res.has_next():
        a, b = res.get_next()
        out.add((a, b))
    return out


def _rel_edges(root: Path) -> set[tuple[str, str]]:
    pre = f"{root}/"
    return {(a.replace(pre, ""), b.replace(pre, "")) for a, b in _edges(root)}


def _callers(root: Path, target: str) -> set[str]:
    return {a for a, b in _rel_edges(root) if b == target}


def _index(root: Path, order: list[str], force: bool = False) -> None:
    for rel in order:
        assert index_file(root / rel, root, force=force)


def test_callee_indexed_after_caller_is_linked(tmp_path, backend):
    _write(tmp_path, {"app.py": APP, "lib.py": LIB, "tests/test_lib.py": TEST})
    _index(tmp_path, ["app.py", "lib.py", "tests/test_lib.py"])
    assert _callers(tmp_path, "lib.py::helper") == {
        "app.py::run",
        "tests/test_lib.py::test_helper",
    }


def test_reindexing_callee_keeps_cross_file_callers(tmp_path, backend):
    _write(tmp_path, {"app.py": APP, "lib.py": LIB, "tests/test_lib.py": TEST})
    _index(tmp_path, ["app.py", "lib.py", "tests/test_lib.py"])
    _write(tmp_path, {"lib.py": 'def helper():\n    """Doc."""\n    return 1\n'})
    _index(tmp_path, ["lib.py"], force=True)
    assert _callers(tmp_path, "lib.py::helper") == {
        "app.py::run",
        "tests/test_lib.py::test_helper",
    }


def test_edges_do_not_depend_on_file_order(tmp_path, backend):
    files = {
        "app.py": APP,
        "lib.py": LIB,
        "tests/test_lib.py": TEST,
        "local.py": LOCAL,
    }
    results = []
    for i, order in enumerate(itertools.permutations(files)):
        root = tmp_path / f"r{i}"
        _write(root, files)
        _index(root, list(order))
        results.append(_rel_edges(root))
        reset_connection()
    assert all(r == results[0] for r in results)
    # Both callers import helper from lib: local.py's helper is not theirs.
    assert results[0] == {
        ("app.py::run", "lib.py::helper"),
        ("tests/test_lib.py::test_helper", "lib.py::helper"),
        ("local.py::user", "local.py::helper"),
    }


@pytest.mark.parametrize("local_first", [True, False])
def test_same_file_definition_wins_both_directions(tmp_path, backend, local_first):
    _write(tmp_path, {"lib.py": LIB, "local.py": LOCAL})
    order = ["local.py", "lib.py"] if local_first else ["lib.py", "local.py"]
    _index(tmp_path, order)
    # Reindexing either side keeps the rule.
    _index(tmp_path, order, force=True)
    assert {b for a, b in _rel_edges(tmp_path) if a == "local.py::user"} == {
        "local.py::helper"
    }
    assert _callers(tmp_path, "lib.py::helper") == set()


def test_editing_caller_updates_outbound_edges(tmp_path, backend):
    _write(
        tmp_path, {"app.py": APP, "lib.py": LIB + "\n\ndef other():\n    return 3\n"}
    )
    _index(tmp_path, ["app.py", "lib.py"])
    assert ("app.py::run", "lib.py::helper") in _rel_edges(tmp_path)

    _write(
        tmp_path,
        {"app.py": "from lib import other\n\n\ndef run():\n    return other()\n"},
    )
    _index(tmp_path, ["app.py"], force=True)
    outbound = {b for a, b in _rel_edges(tmp_path) if a == "app.py::run"}
    assert outbound == {"lib.py::other"}

    # The stale call site is gone: reindexing lib.py must not bring it back.
    _index(tmp_path, ["lib.py"], force=True)
    outbound = {b for a, b in _rel_edges(tmp_path) if a == "app.py::run"}
    assert outbound == {"lib.py::other"}


def test_deleted_callee_drops_edges_and_keeps_other_definition(tmp_path, backend):
    _write(tmp_path, {"app.py": STAR, "lib.py": LIB, "alt.py": LIB})
    _index(tmp_path, ["app.py", "lib.py", "alt.py"])
    assert {b for a, b in _rel_edges(tmp_path) if a == "app.py::run"} == {
        "lib.py::helper",
        "alt.py::helper",
    }
    conn = get_connection(tmp_path)
    conn.delete_file_completely(str(tmp_path / "lib.py"))
    assert {b for a, b in _rel_edges(tmp_path) if a == "app.py::run"} == {
        "alt.py::helper"
    }
    # A definition appearing again is linked from the stored call site,
    # without reindexing the caller.
    _write(tmp_path, {"lib.py": LIB})
    _index(tmp_path, ["lib.py"])
    assert {b for a, b in _rel_edges(tmp_path) if a == "app.py::run"} == {
        "alt.py::helper",
        "lib.py::helper",
    }


def test_deleted_caller_drops_its_call_sites(tmp_path, backend):
    _write(tmp_path, {"app.py": APP, "lib.py": LIB})
    _index(tmp_path, ["app.py", "lib.py"])
    get_connection(tmp_path).delete_file_completely(str(tmp_path / "app.py"))
    _index(tmp_path, ["lib.py"], force=True)
    assert _callers(tmp_path, "lib.py::helper") == set()


def _simulate_pre_upgrade_index(root: Path) -> None:
    """Leave the graph the way 0.15.0 left it: no call sites, the cross-file
    edges lost to a reindex of the callee, a scan record without a format."""
    conn = get_connection(root)
    conn.execute("DELETE FROM call_site")
    conn.execute("DELETE FROM edge_calls")
    meta_path = root / ".codegraph" / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("graph_format", None)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_full_index_after_upgrade_reparses_unchanged_files(tmp_path, backend):
    _write(tmp_path, {"app.py": APP, "lib.py": LIB, "tests/test_lib.py": TEST})
    index_repo(tmp_path, method="os_walk")
    assert read_meta(tmp_path)["graph_format"] == GRAPH_FORMAT
    _simulate_pre_upgrade_index(tmp_path)

    # Every file is unchanged, so only the format upgrade makes this reparse.
    index_repo(tmp_path, method="os_walk")
    assert _callers(tmp_path, "lib.py::helper") == {
        "app.py::run",
        "tests/test_lib.py::test_helper",
    }
    assert read_meta(tmp_path)["graph_format"] == GRAPH_FORMAT


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_incremental_after_upgrade_falls_back_to_full(tmp_path, backend):
    _write(tmp_path, {"app.py": APP, "lib.py": LIB, "tests/test_lib.py": TEST})
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=tmp_path, check=True)
    index_repo(tmp_path)
    _simulate_pre_upgrade_index(tmp_path)

    from codegraph.server import _startup_index_needed

    assert _startup_index_needed(tmp_path, reindex=False)
    stats = incremental_reindex(tmp_path)
    assert stats["mode"] == "fallback_full"
    assert _callers(tmp_path, "lib.py::helper") == {
        "app.py::run",
        "tests/test_lib.py::test_helper",
    }
    assert not _startup_index_needed(tmp_path, reindex=False)
    assert incremental_reindex(tmp_path)["mode"] == "incremental"
