# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the opt-in jedi-backed precise CALLS resolver. The
#              cross-file case proves an edge the name-matched resolver could
#              never draw (it is same-file only). The flag-off case proves the
#              default path is unchanged.

from __future__ import annotations

import sys

import pytest

from codegraph.core.config import load_config
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file

# Importing jedi pulls in parso, which lowers sys.recursionlimit to 3000 on
# import. The indexer raises it to 10_000 (deep tree-sitter walks). Restore
# the higher value after the skip-guard import so this module never weakens
# the limit for tests that run later in the same session.
_prior_limit = sys.getrecursionlimit()
pytest.importorskip("jedi")
if sys.getrecursionlimit() < _prior_limit:
    sys.setrecursionlimit(_prior_limit)


@pytest.fixture(autouse=True)
def clean_db():
    reset_connection()
    yield
    reset_connection()


def _write_config(root, body: str) -> None:
    cg = root / ".codegraph"
    cg.mkdir(exist_ok=True)
    (cg / "config.toml").write_text(body, encoding="utf-8")


def _calls_targets(conn, caller_id: str) -> set[str]:
    edges = conn.find_neighbors("CALLS", src_key=caller_id, return_dst=["id"])
    return {e["dst_id"] for e in edges}


def test_precise_calls_resolves_cross_file(tmp_path):
    # a.py's caller() calls helper() DEFINED IN b.py. The name-matched
    # resolver is same-file only and would never link this; jedi follows the
    # import to b.py and the CALLS edge points across files.
    _write_config(tmp_path, "[codegraph]\nprecise_calls = true\n")
    (tmp_path / "b.py").write_text(
        "def helper():\n    return 2\n",
        encoding="utf-8",
    )
    a_py = tmp_path / "a.py"
    a_py.write_text(
        "from b import helper\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.precise_calls is True

    index_file(tmp_path / "b.py", tmp_path, cfg=cfg)
    index_file(a_py, tmp_path, cfg=cfg)

    conn = get_connection(tmp_path)
    caller_id = f"{a_py}::caller"
    targets = _calls_targets(conn, caller_id)
    assert f"{tmp_path / 'b.py'}::helper" in targets


def test_precise_calls_beats_same_name_collision(tmp_path):
    # The decisive case the name matcher CANNOT get right. b.py defines
    # helper(); a.py imports b.helper, also defines its OWN unrelated helper(),
    # and caller() calls the imported one. The name matcher prefers the
    # same-file helper (a.py::helper); jedi follows the call to b.py::helper.
    _write_config(tmp_path, "[codegraph]\nprecise_calls = true\n")
    (tmp_path / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    a_py = tmp_path / "a.py"
    a_py.write_text(
        "import b\n\n\n"
        "def helper():\n    return 99\n\n\n"
        "def caller():\n    return b.helper()\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    index_file(tmp_path / "b.py", tmp_path, cfg=cfg)
    index_file(a_py, tmp_path, cfg=cfg)

    conn = get_connection(tmp_path)
    targets = _calls_targets(conn, f"{a_py}::caller")
    # Precise: resolves to b.py's helper, NOT a.py's same-named decoy.
    assert f"{tmp_path / 'b.py'}::helper" in targets
    assert f"{a_py}::helper" not in targets


def test_precise_calls_resolves_cross_file_method(tmp_path):
    # Method resolution: a.py calls w.run() on a Worker defined in b.py. The
    # edge must use the Class.method id scheme ("b.py::Worker.run").
    _write_config(tmp_path, "[codegraph]\nprecise_calls = true\n")
    (tmp_path / "b.py").write_text(
        "class Worker:\n    def run(self):\n        return 5\n",
        encoding="utf-8",
    )
    a_py = tmp_path / "a.py"
    a_py.write_text(
        "from b import Worker\n\n\ndef caller():\n    w = Worker()\n    return w.run()\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    index_file(tmp_path / "b.py", tmp_path, cfg=cfg)
    index_file(a_py, tmp_path, cfg=cfg)

    conn = get_connection(tmp_path)
    targets = _calls_targets(conn, f"{a_py}::caller")
    assert f"{tmp_path / 'b.py'}::Worker.run" in targets


def test_precise_calls_via_env(tmp_path, monkeypatch):
    # The env override drives the same behavior as the TOML flag.
    monkeypatch.setenv("CGH_PRECISE_CALLS", "1")
    (tmp_path / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    a_py = tmp_path / "a.py"
    a_py.write_text(
        "from b import helper\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.precise_calls is True
    index_file(tmp_path / "b.py", tmp_path, cfg=cfg)
    index_file(a_py, tmp_path, cfg=cfg)

    conn = get_connection(tmp_path)
    targets = _calls_targets(conn, f"{a_py}::caller")
    assert f"{tmp_path / 'b.py'}::helper" in targets


def test_flag_off_collision_resolves_to_same_file(tmp_path):
    # With the flag OFF (default) the name-matched resolver runs. Its defining
    # trait is same-file preference on name collisions: b.py and a.py both
    # define helper(), and a.py's caller() must link to a.py's helper only,
    # never b.py's. This is the exact pre-existing behavior, unchanged.
    (tmp_path / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    a_py = tmp_path / "a.py"
    a_py.write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.precise_calls is False

    index_file(tmp_path / "b.py", tmp_path, cfg=cfg)
    index_file(a_py, tmp_path, cfg=cfg)

    conn = get_connection(tmp_path)
    targets = _calls_targets(conn, f"{a_py}::caller")
    assert targets == {f"{a_py}::helper"}
