# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: End to end, once per graph backend: index a tmp repo, start a
#              real owner with its file watcher, call MCP tools over HTTP the
#              way the CLI does, edit files and check the watcher's reindex
#              shows up in find_callers, then always stop the owner.

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from codegraph.cli.owner_client import call_owner_tool
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner lifecycle")

LIB = 'def helper():\n    """Shared helper."""\n    return 1\n'
APP = "from lib import helper\n\n\ndef run():\n    return helper()\n"


def _call(root: Path, tool: str, **arguments):
    reply = call_owner_tool(str(root), tool, arguments, timeout=30)
    assert reply.ok, f"{tool}: {reply.status} {reply.error}"
    return reply.data


def _caller_names(root: Path, fn_name: str) -> set[str]:
    data = _call(root, "find_callers", fn_name=fn_name)
    return {row["caller"] for row in data["callers"]}


def _wait_for(predicate, what: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    pytest.fail(f"watcher never reflected: {what}")


@pytest.mark.parametrize("backend", ["duckdb", "sqlite"])
def test_owner_serves_and_watcher_reindexes(tmp_path, monkeypatch, backend):
    if backend == "duckdb":
        pytest.importorskip("duckdb")
    from codegraph.state.ipc import (
        register_keepalive,
        spawn_owner,
        stop_owner,
        unregister_keepalive,
    )

    # The owner subprocess inherits the environment, so it opens the same
    # backend this test indexed with.
    monkeypatch.setenv("CGH_DB", backend)
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    (root / "lib.py").write_text(LIB, encoding="utf-8")
    (root / "app.py").write_text(APP, encoding="utf-8")
    reset_connection()
    index_repo(root, method="os_walk")
    reset_connection()
    graph_file = "graph.duckdb" if backend == "duckdb" else "graph.sqlite"
    assert (root / ".codegraph" / graph_file).exists()

    register_keepalive(root)
    try:
        port = spawn_owner(root, watch=True, reindex=False)
        assert port, "owner did not come up"

        found = _call(root, "symbol_lookup", name="helper")
        assert "lib.py" in json.dumps(found)
        assert _caller_names(root, "helper") == {"run"}

        hits = _call(root, "fts_search", query="Shared helper")
        assert "helper" in json.dumps(hits)

        _call(
            root,
            "knowledge_record",
            title="E2E owner note",
            body="the watcher relinks callers after a callee save",
            kind="note",
            tags="e2e",
        )
        notes = _call(root, "knowledge_search", query="watcher relinks callers")
        assert "E2E owner note" in json.dumps(notes)

        # Save the callee's file: the cross-file caller must survive it.
        (root / "lib.py").write_text(
            LIB + "\n\ndef helper_two():\n    return 2\n", encoding="utf-8"
        )
        _wait_for(
            lambda: (
                "helper_two"
                in json.dumps(_call(root, "symbol_lookup", name="helper_two"))
            ),
            "new function in lib.py",
        )
        assert _caller_names(root, "helper") == {"run"}

        # Save the caller's file with a second call site.
        (root / "app.py").write_text(
            APP + "\n\ndef run_twice():\n    return helper() + helper()\n",
            encoding="utf-8",
        )
        _wait_for(
            lambda: _caller_names(root, "helper") == {"run", "run_twice"},
            "new caller in app.py",
        )
    finally:
        unregister_keepalive(root)
        stop_owner(root)
        reset_connection()
