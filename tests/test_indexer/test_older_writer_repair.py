# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A rollback to cgh 0.15 that edits files of a current index (its
#              watcher, a single-file index) rewrites their nodes but keeps no
#              call sites, name references or stamp, and its purge leaves the
#              ones recorded before. The next run of this version must find
#              those files and parse them again, so the graph ends up as a
#              clean index would build it. Also covers a full index killed
#              halfway through a format upgrade. Run on both backends.

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from codegraph import indexer
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import incremental_reindex, index_file, index_repo
from codegraph.state.scan_meta import GRAPH_FORMAT, read_meta

_SIDE = {"call_site": "file_path", "name_ref": "file_path", "file_stamp": "path"}

LIB = "def helper():\n    return 1\n\n\nclass Base:\n    pass\n"
APP = (
    "from lib import Base, helper\n\n\n"
    "def run():\n    return helper()\n\n\n"
    "class Sub(Base):\n    pass\n"
)
# What the older writer saves: run() now calls fresh() instead of helper().
APP_EDITED = (
    "from lib import Base, fresh\n\n\n"
    "def run():\n    return fresh()\n\n\n"
    "class Sub(Base):\n    pass\n"
)
LIB_EDITED = LIB + "\n\ndef fresh():\n    return 2\n"


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    yield request.param
    reset_connection()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _rows(root: Path, sql: str) -> set[tuple]:
    res = get_connection(root).execute(sql)
    pre = f"{root}/"
    out = set()
    while res.has_next():
        out.add(tuple(str(v).replace(pre, "") for v in res.get_next()))
    return out


def _graph(root: Path) -> dict[str, set[tuple]]:
    return {
        "calls": _rows(root, "SELECT from_id, to_id FROM edge_calls"),
        "inherits": _rows(root, "SELECT from_id, to_id FROM edge_inherits"),
        "call_site": _rows(root, "SELECT from_id, file_path, name FROM call_site"),
        "name_ref": _rows(root, "SELECT kind, from_id, file_path, name FROM name_ref"),
    }


def _side_rows(root: Path, path: str) -> dict[str, list[tuple]]:
    conn = get_connection(root)
    out = {}
    for table, col in _SIDE.items():
        res = conn.execute(f"SELECT * FROM {table} WHERE {col} = '{path}'")
        out[table] = []
        while res.has_next():
            out[table].append(tuple(res.get_next()))
    return out


def _restore_side_rows(root: Path, path: str, rows: dict[str, list[tuple]]) -> None:
    conn = get_connection(root)
    for table, col in _SIDE.items():
        conn.execute(f"DELETE FROM {table} WHERE {col} = '{path}'")
        for row in rows[table]:
            values = ", ".join(
                "NULL" if v is None else repr(v) if not isinstance(v, str) else f"'{v}'"
                for v in row
            )
            conn.execute(f"INSERT INTO {table} VALUES ({values})")


def _older_writer_index(root: Path, rel: str, body: str | None) -> None:
    """What cgh 0.15 leaves after it saves (body) or deletes (None) a file:
    the file's nodes and outbound edges rewritten, edges from other files
    into it lost (0.15 has no inbound pass), and the call sites, name
    references and stamp of the file left exactly as they were."""
    path = str(root / rel)
    kept = _side_rows(root, path)
    conn = get_connection(root)
    if body is None:
        (root / rel).unlink()
        conn.delete_file_completely(path)
    else:
        (root / rel).write_text(body, encoding="utf-8")
        assert index_file(root / rel, root, force=True)
        for table in ("edge_calls", "edge_inherits"):
            conn.execute(
                f"DELETE FROM {table} WHERE to_id LIKE '{path}::%' "
                f"AND from_id NOT LIKE '{path}::%'"
            )
    _restore_side_rows(root, path, kept)


def _clean_graph(tmp_path: Path, files: dict[str, str]) -> dict[str, set[tuple]]:
    twin = tmp_path / "twin"
    twin.mkdir()
    _write(twin, files)
    index_repo(twin, method="os_walk")
    graph = _graph(twin)
    reset_connection()
    return graph


def test_files_saved_by_an_older_writer_are_parsed_again(tmp_path, backend):
    final = {"lib.py": LIB_EDITED, "app.py": APP_EDITED}
    expected = _clean_graph(tmp_path, final)
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, {"lib.py": LIB, "app.py": APP})
    index_repo(root, method="os_walk")
    assert get_connection(root).unstamped_files() == ([], [])

    _older_writer_index(root, "app.py", APP_EDITED)
    _older_writer_index(root, "lib.py", LIB_EDITED)
    stale, orphans = get_connection(root).unstamped_files()
    assert sorted(Path(p).name for p in stale) == ["app.py", "lib.py"]
    assert orphans == []
    # The damage: app.py still holds its old call site to helper, and the
    # edges from app.py into lib.py were lost when lib.py was saved.
    damaged = _graph(root)
    assert ("app.py::run", "app.py", "helper") in damaged["call_site"]
    assert damaged["inherits"] == set()
    assert ("app.py::run", "lib.py::fresh") not in damaged["calls"]

    index_repo(root, method="os_walk")
    assert _graph(root) == expected
    assert get_connection(root).unstamped_files() == ([], [])
    assert read_meta(root)["graph_format"] == GRAPH_FORMAT


def test_file_deleted_by_an_older_writer_leaves_no_ghost_caller(tmp_path, backend):
    _write(tmp_path, {"lib.py": LIB, "app.py": APP})
    index_repo(tmp_path, method="os_walk")
    _older_writer_index(tmp_path, "app.py", None)
    assert get_connection(tmp_path).unstamped_files()[1] == [str(tmp_path / "app.py")]

    index_repo(tmp_path, method="os_walk")
    # A save of the callee relinks callers from the stored sites: none left.
    assert index_file(tmp_path / "lib.py", tmp_path, force=True)
    graph = _graph(tmp_path)
    assert graph["calls"] == set()
    assert graph["call_site"] == set()
    assert graph["inherits"] == set()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_incremental_parses_files_an_older_writer_saved(tmp_path, backend):
    final = {"lib.py": LIB_EDITED, "app.py": APP_EDITED}
    expected = _clean_graph(tmp_path, final)
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, {"lib.py": LIB, "app.py": APP, ".gitignore": ".codegraph/\n"})
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "a"], cwd=root, check=True)
    index_repo(root)

    _older_writer_index(root, "lib.py", LIB_EDITED)
    _older_writer_index(root, "app.py", APP_EDITED)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "b"], cwd=root, check=True)
    # The older writer stored the new blob shas: the diff finds nothing.
    for rel in ("lib.py", "app.py"):
        sha = subprocess.run(
            ["git", "hash-object", rel], cwd=root, capture_output=True, text=True
        ).stdout.strip()
        get_connection(root).upsert_node(
            "File", "path", str(root / rel), {"git_blob_sha": sha}
        )

    result = incremental_reindex(root)
    assert result["mode"] == "incremental"
    assert sorted(result["reindexed"]) == ["app.py", "lib.py"]
    assert _graph(root) == expected


def test_owner_start_reindexes_when_an_older_writer_touched_files(tmp_path, backend):
    from codegraph.server import _startup_index_needed

    _write(tmp_path, {"lib.py": LIB, "app.py": APP})
    index_repo(tmp_path, method="os_walk")
    assert _startup_index_needed(tmp_path, reindex=False) is False
    _older_writer_index(tmp_path, "lib.py", LIB_EDITED)
    assert _startup_index_needed(tmp_path, reindex=False) is True


def test_full_upgrade_killed_halfway_restarts_and_completes(
    tmp_path, backend, monkeypatch
):
    files = {f"m{i}.py": f"def f{i}():\n    return f{i + 1}()\n" for i in range(6)}
    files["m6.py"] = "def f6():\n    return 0\n"
    expected = _clean_graph(tmp_path, files)
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, files)
    index_repo(root, method="os_walk")
    meta_path = root / ".codegraph" / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["graph_format"] = GRAPH_FORMAT - 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    get_connection(root).execute("DELETE FROM call_site")

    real = indexer.index_file
    calls = {"n": 0}

    def dying(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:
            raise KeyboardInterrupt  # SIGINT / SIGTERM mid-scan
        return real(*args, **kwargs)

    monkeypatch.setattr(indexer, "index_file", dying)
    with pytest.raises(KeyboardInterrupt):
        index_repo(root, method="os_walk")
    monkeypatch.setattr(indexer, "index_file", real)
    # Nothing claims the new format while the graph is half rebuilt.
    assert read_meta(root)["graph_format"] == GRAPH_FORMAT - 1

    index_repo(root, method="os_walk")
    assert read_meta(root)["graph_format"] == GRAPH_FORMAT
    assert _graph(root) == expected
