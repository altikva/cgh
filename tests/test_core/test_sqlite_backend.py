# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The SQLite graph backend indexes and queries correctly, and is
#              graph-equivalent to DuckDB on the same source (the DuckDB<->SQLite
#              parity gate). CGH_DB=sqlite selects it; graph.sqlite is written;
#              counts and a call traversal match DuckDB exactly.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.core.db import get_readonly_connection, reset_connection
from codegraph.core.graph_model import EDGES, NODES
from codegraph.indexer import index_repo

SRC = (
    "class Repo:\n"
    "    def add(self, k, v):\n"
    "        return store(k, v)\n"
    "\n"
    "def store(k, v):\n"
    "    return (k, v)\n"
    "\n"
    "def top():\n"
    "    return Repo().add(1, 2)\n"
)


def _mk_repo(tmp_path: Path) -> Path:
    import subprocess

    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(SRC, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


def _counts(root: Path) -> dict[str, int]:
    conn = get_readonly_connection(root)
    assert conn is not None
    out = {f"N:{lbl}": conn.count_nodes(lbl) for lbl in NODES}
    out.update({f"E:{et}": conn.count_edges(et) for et in EDGES})
    reset_connection()
    return out


def test_sqlite_indexes_and_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("CGH_DB", "sqlite")
    root = _mk_repo(tmp_path)
    reset_connection()
    index_repo(str(root))
    reset_connection()

    assert (root / ".codegraph" / "graph.sqlite").exists()
    conn = get_readonly_connection(root)
    assert conn is not None
    assert conn.count_nodes("Function") == 3  # add (method), store, top
    assert conn.count_nodes("Class") == 1  # Repo
    # top() calls Repo().add(); add() calls store() -> CALLS edges exist
    callers = conn.find_neighbors(
        "CALLS", dst_where={"name": "store"}, return_src=["name"]
    )
    assert {c["src_name"] for c in callers} == {"add"}
    reset_connection()


def test_sqlite_matches_duckdb(tmp_path, monkeypatch):
    # Same source, both backends, identical graph: the parity gate.
    root_d = _mk_repo(tmp_path / "d")
    monkeypatch.setenv("CGH_DB", "duckdb")
    reset_connection()
    index_repo(str(root_d))
    reset_connection()
    duck = _counts(root_d)

    root_s = _mk_repo(tmp_path / "s")
    monkeypatch.setenv("CGH_DB", "sqlite")
    reset_connection()
    index_repo(str(root_s))
    reset_connection()
    lite = _counts(root_s)

    assert lite == duck, (
        f"parity mismatch: { {k: (duck[k], lite[k]) for k in duck if duck[k] != lite.get(k)} }"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
