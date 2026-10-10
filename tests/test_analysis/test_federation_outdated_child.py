# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A federated child whose index is in an older graph format is
#              left out of the fan-out with a warning naming the remedy,
#              instead of answering short from a graph missing tables and
#              edges. A child at the current format is queried as before.

from __future__ import annotations

import json
from pathlib import Path

from codegraph.analysis.federation import _outdated_child, _run_one_graphdb
from codegraph.state.scan_meta import GRAPH_FORMAT


def _child(tmp_path: Path, name: str, graph_format: int | None) -> Path:
    root = tmp_path / name
    cg = root / ".codegraph"
    cg.mkdir(parents=True)
    meta: dict = {"root": str(root.resolve())}
    if graph_format is not None:
        meta["graph_format"] = graph_format
    (cg / "scan_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return root


def test_outdated_child_is_left_out_with_the_remedy(tmp_path):
    parent = tmp_path.resolve()
    old = _child(tmp_path, "old", None)  # a record from before versioning
    called: list[Path] = []
    res = _run_one_graphdb(old, parent, lambda conn, r: called.append(r))
    assert not called
    assert res.scope == "old" and res.payload is None
    assert "cgh index" in res.error and f"needs {GRAPH_FORMAT}" in res.error


def test_current_child_is_not_flagged(tmp_path):
    assert _outdated_child(_child(tmp_path, "new", GRAPH_FORMAT)) == ""
    assert _outdated_child(tmp_path / "no-meta") == ""


def test_the_parent_scope_is_never_skipped(tmp_path):
    parent = _child(tmp_path, "parent", None)
    res = _run_one_graphdb(parent, parent, lambda conn, r: "ran")
    # No graph file here, so the open fails, but not on the format check.
    assert res.error is None or "graph format" not in res.error
