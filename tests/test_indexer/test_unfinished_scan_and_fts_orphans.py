# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A half-built store gets finished, and stays consistent: FTS
#              rows of files the graph no longer holds are purged by the next
#              index, an owner indexes a store with no completed scan even
#              without --reindex, init --from refuses to seed under a running
#              index, and cgh reset only sweeps its own repo's owner.

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codegraph.core.fts import get_fts_conn, upsert_symbol
from codegraph.indexer import incremental_reindex, index_repo


def _git_repo(root: Path) -> None:
    (root / "mod.py").write_text("def kept():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )


def _fts_files(root: Path) -> set[str]:
    conn = get_fts_conn(root)
    return {p for (p,) in conn.execute("SELECT DISTINCT file_path FROM symbols")}


def _plant_orphan(root: Path) -> str:
    ghost = str(root / "api" / "gone.py")
    conn = get_fts_conn(root)
    upsert_symbol(conn, f"{ghost}::ghost", "function", "ghost", ghost, 1, 2, "")
    conn.commit()
    return ghost


@pytest.mark.parametrize("method", ["full", "incremental"])
def test_index_purges_fts_rows_of_files_not_in_the_graph(tmp_path, method):
    _git_repo(tmp_path)
    index_repo(str(tmp_path))
    ghost = _plant_orphan(tmp_path)
    assert ghost in _fts_files(tmp_path)

    if method == "full":
        index_repo(str(tmp_path), method="git_ls_files")
    else:
        incremental_reindex(str(tmp_path))

    files = _fts_files(tmp_path)
    assert ghost not in files
    assert str(tmp_path / "mod.py") in files


def test_owner_indexes_a_store_with_no_completed_scan(tmp_path):
    from codegraph.server import _startup_index_needed

    (tmp_path / ".codegraph").mkdir()
    assert _startup_index_needed(tmp_path, reindex=False)
    from codegraph.state.scan_meta import GRAPH_FORMAT

    meta = tmp_path / ".codegraph" / "scan_meta.json"
    meta.write_text(f'{{"git_head": "abc", "graph_format": {GRAPH_FORMAT}}}')
    assert not _startup_index_needed(tmp_path, reindex=False)
    assert _startup_index_needed(tmp_path, reindex=True)
    # A scan from an older graph format needs one full re-parse.
    meta.write_text('{"git_head": "abc"}')
    assert _startup_index_needed(tmp_path, reindex=False)


def test_seed_refuses_while_an_index_runs_on_the_target(tmp_path):
    from codegraph.cli.commands_init import _seed_from_checkout

    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / ".codegraph").mkdir(parents=True)
    (dst / ".codegraph").mkdir(parents=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (dst / ".codegraph" / "index.lock").write_text(f"{other.pid}\n")
        with pytest.raises(SystemExit):
            _seed_from_checkout(dst, src)
    finally:
        other.kill()
        other.wait()


def test_reset_sweeps_only_its_own_repo_owner(monkeypatch, tmp_path):
    from codegraph.cli import commands_monitor

    mine, sibling = tmp_path / "api", tmp_path / "api-1250"
    listing = (
        f"101 python -m codegraph _serve_owner --root {mine} --watch\n"
        f"102 python -m codegraph _serve_owner --root {sibling} --watch\n"
        f"103 python -m codegraph serve --root {mine}\n"
    )

    class _Done:
        stdout = listing

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done())
    assert commands_monitor._stray_owner_pids(Path(os.path.abspath(mine))) == [101]
