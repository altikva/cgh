# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A query run on an index from an older cgh, before any reindex,
#              hits tables missing the columns this version reads. The CLI
#              must name the remedy (`cgh index`) instead of printing the
#              database error, and leave every other error untouched.

from __future__ import annotations

import argparse
import json

import pytest

from codegraph.__main__ import _outdated_store_hint
from codegraph.state.scan_meta import GRAPH_FORMAT


class BinderException(Exception):
    """Stands in for duckdb.BinderException, matched by class name."""


def _store(tmp_path, graph_format):
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    meta = {"root": str(tmp_path.resolve())}
    if graph_format is not None:
        meta["graph_format"] = graph_format
    (cg / "scan_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return argparse.Namespace(root=str(tmp_path))


def test_old_store_schema_error_names_the_remedy(tmp_path):
    args = _store(tmp_path, None)  # a record that predates versioning
    exc = BinderException('Referenced column "address" not found in FROM clause')
    hint = _outdated_store_hint(args, exc)
    assert "cgh index" in hint and f"needs {GRAPH_FORMAT}" in hint


@pytest.mark.parametrize(
    "graph_format, exc",
    [
        (GRAPH_FORMAT, BinderException('Referenced column "x" not found')),
        (1, ValueError("column of a different kind")),
        (1, BinderException("something unrelated")),
    ],
)
def test_other_errors_are_left_alone(tmp_path, graph_format, exc):
    assert _outdated_store_hint(_store(tmp_path, graph_format), exc) == ""
