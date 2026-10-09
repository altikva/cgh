# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A .codegraph store copied to another directory, then indexed
#              with a full `cgh index`, must not keep any node of the old root.
#              Before, the full walk upserted the new paths beside the old ones
#              and every caller was reported twice.

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import incremental_reindex, index_repo
from codegraph.state.scan_meta import clear_meta, read_meta

FILES = {
    "pkg/helpers.py": "def normalize(x):\n    return x\n",
    "pkg/use.py": (
        "from pkg.helpers import normalize\n\n\ndef run():\n    return normalize(1)\n"
    ),
}


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    monkeypatch.setenv("CGH_DB", request.param)
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    yield request.param
    reset_connection()


def _make(root: Path) -> None:
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _copy(tmp_path: Path) -> tuple[Path, Path]:
    old = tmp_path / "old"
    new = tmp_path / "new"
    _make(old)
    index_repo(old, method="os_walk")
    reset_connection()
    shutil.copytree(old, new)
    return old.resolve(), new.resolve()


def _assert_clean(new: Path, old: Path) -> None:
    conn = get_connection(new)
    paths = [p for (p,) in conn.list_node_fields("File", ["path"])]
    assert paths and all(p.startswith(str(new) + "/") for p in paths), paths
    res = conn.execute("SELECT from_id, to_id FROM edge_calls")
    rows = []
    while res.has_next():
        rows.append(tuple(res.get_next()))
    into = [r for r in rows if str(r[1]).endswith("::normalize")]
    assert len(into) == 1, rows
    assert all(str(old) not in str(v) for r in rows for v in r), rows
    fts = sqlite3.connect(new / ".codegraph" / "fts.db")
    try:
        fts_paths = {
            p for (p,) in fts.execute("SELECT DISTINCT file_path FROM symbols")
        }
    finally:
        fts.close()
    assert fts_paths and all(p.startswith(str(new)) for p in fts_paths), fts_paths


def test_full_index_of_a_copied_store_rebuilds(tmp_path, backend):
    old, new = _copy(tmp_path)
    assert read_meta(new)["root"] == str(old)
    index_repo(new)
    _assert_clean(new, old)
    assert read_meta(new)["root"] == str(new)


def test_copied_store_without_recorded_root_rebuilds(tmp_path, backend):
    old, new = _copy(tmp_path)
    clear_meta(new)  # an older writer recorded no root
    index_repo(new)
    _assert_clean(new, old)


def test_incremental_of_a_copied_store_without_meta_rebuilds(tmp_path, backend):
    old, new = _copy(tmp_path)
    clear_meta(new)
    incremental_reindex(new)
    _assert_clean(new, old)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """What `cgh init --from` does for a ticket worktree: a seed checkout is
    indexed once, the ticket is a `git worktree add` of it, and the seed's
    store is copied over and rewritten to the ticket's root."""
    from codegraph.state.relocate import relocate_store

    seed = tmp_path / "seed"
    ticket = tmp_path / "ticket"
    _make(seed)
    (seed / ".gitignore").write_text(".codegraph/\n")
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "init")
    index_repo(seed)
    reset_connection()
    _git(seed, "worktree", "add", "-q", "-b", "ticket", str(ticket))
    (ticket / ".codegraph").mkdir()
    relocate_store(seed, ticket)
    reset_connection()
    return seed.resolve(), ticket.resolve()


def test_seeded_worktree_stays_incremental(tmp_path, backend, monkeypatch):
    # A seed rewrites the recorded root, so the copied-store check trusts it
    # and the reindex that follows the seed parses only what the ticket
    # branch changed, instead of rebuilding the whole graph.
    import codegraph.indexer as indexer

    seed, ticket = _seed(tmp_path)
    assert read_meta(ticket)["root"] == str(ticket)
    (ticket / "pkg" / "extra.py").write_text("def extra():\n    return 2\n")
    _git(ticket, "add", "pkg/extra.py")
    _git(ticket, "commit", "-q", "-m", "ticket work")

    verdicts: list[str] = []
    real_foreign = indexer._foreign_root
    monkeypatch.setattr(
        indexer,
        "_foreign_root",
        lambda root: verdicts.append(real_foreign(root)) or verdicts[-1],
    )
    stats = incremental_reindex(ticket)
    assert not any(verdicts), verdicts
    assert stats["mode"] == "incremental", stats
    assert [Path(p).name for p in stats["reindexed"]] == ["extra.py"], stats
    assert stats["unchanged_count"] >= 2, stats
    _assert_clean(ticket, seed)


def test_extra_dir_outside_the_root_is_not_foreign(tmp_path, backend):
    from codegraph.indexer import _foreign_root

    root = tmp_path / "repo"
    _make(root)
    side = tmp_path / "side"
    side.mkdir()
    (side / "s.py").write_text("def s():\n    return 1\n", encoding="utf-8")
    cfg = root / ".codegraph" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[codegraph]\nextra_dirs = ["../side"]\n', encoding="utf-8")
    index_repo(root, method="os_walk")
    clear_meta(root)
    assert _foreign_root(root) == ""
