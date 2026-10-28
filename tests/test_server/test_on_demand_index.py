# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: On-demand indexing inside the owner: the MCP tools that resolve
#              to a repo file the graph lacks index it and answer, saying so;
#              a gitignored file is never indexed; and the on-demand path for
#              a typical 40 KB Python file stays within its budget.

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import on_demand
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_insight import register as register_insight
from codegraph.server.tools_query import register as register_query


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A git repo with one indexed file, as the owner sees it."""
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home-cg")
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("gen/\n.codegraph/\n")
    reset_connection()
    _srv._root = repo
    _srv._conn = None
    on_demand._last_catch_up.clear()
    seed = repo / "seed.py"
    seed.write_text("def seed():\n    return 0\n")
    index_file(seed, repo)
    yield repo
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _tools() -> dict:
    m = _FakeMcp()
    register_insight(m)
    register_query(m)
    return m.tools


def test_symbol_lookup_finds_a_just_created_file(root):
    (root / "fresh.py").write_text(
        "def brand_new_helper(x):\n    return x * 2\n\n\n"
        "def caller():\n    return brand_new_helper(1)\n"
    )
    out = json.loads(_tools()["symbol_lookup"]("brand_new_helper"))
    assert out["found"] is True
    assert out["definitions"][0]["file"].endswith("fresh.py")
    assert out["indexed_now"] == ["fresh.py"]
    assert "indexed just now" in out["note"]


def test_find_callers_catches_up_on_a_new_caller(root):
    (root / "user.py").write_text(
        "from seed import seed\n\n\ndef uses_seed():\n    return seed()\n"
    )
    out = json.loads(_tools()["find_callers"]("seed"))
    assert any(c.get("caller", "").endswith("uses_seed") for c in out["callers"])
    assert out["indexed_now"] == ["user.py"]


def test_symbol_lookup_hit_does_not_index(root):
    (root / "other.py").write_text("def other():\n    return 1\n")
    out = json.loads(_tools()["symbol_lookup"]("seed"))
    assert "indexed_now" not in out
    conn = get_connection(root)
    assert (
        conn.query_node_field("File", "path", str(root / "other.py"), "mtime") is None
    )


def test_file_summary_indexes_an_unindexed_file(root):
    (root / "mod.py").write_text("def alpha():\n    return 1\n")
    out = json.loads(_tools()["file_summary"]("mod.py"))
    assert out["found"] is True
    assert out["indexed_now"] is True
    assert out["on_demand"] == "indexed"
    assert [f["name"] for f in out["functions"]] == ["alpha"]
    # Indexed now: the next call finds it fresh.
    again = json.loads(_tools()["file_summary"]("mod.py"))
    assert "indexed_now" not in again


def test_file_summary_reindexes_a_changed_file(root):
    seed = root / "seed.py"
    seed.write_text("def seed():\n    return 0\n\n\ndef sprout():\n    return 1\n")
    out = json.loads(_tools()["file_summary"]("seed.py"))
    assert out["indexed_now"] is True
    assert {f["name"] for f in out["functions"]} == {"seed", "sprout"}


def test_gitignored_file_is_never_indexed(root):
    gen = root / "gen" / "client.py"
    gen.parent.mkdir()
    gen.write_text("def generated():\n    return 1\n")
    out = json.loads(_tools()["file_summary"]("gen/client.py"))
    assert out["found"] is False
    assert out["on_demand"] == "ignored"
    assert on_demand.ensure_indexed(root, gen) == "ignored"
    lookup = json.loads(_tools()["symbol_lookup"]("generated"))
    assert lookup["found"] is False


def test_file_outside_the_repo_is_not_indexed(root, tmp_path):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("def far():\n    return 1\n")
    assert on_demand.ensure_indexed(root, outside) == "transient"
    conn = get_connection(root)
    assert conn.query_node_field("File", "path", str(outside), "mtime") is None


def test_home_directory_root_is_never_written(root, monkeypatch):
    (root / "home_file.py").write_text("def at_home():\n    return 1\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    assert not on_demand.writable_root(root)
    assert on_demand.ensure_indexed(root, root / "home_file.py") == "transient"
    assert on_demand.catch_up(root) == []
    conn = get_connection(root)
    assert (
        conn.query_node_field("File", "path", str(root / "home_file.py"), "mtime")
        is None
    )


def test_root_without_git_is_never_written(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not on_demand.writable_root(plain)


def test_busy_index_lock_gives_up(root, monkeypatch):
    import threading

    from codegraph.state import index_lock

    (root / "late.py").write_text("def late():\n    return 1\n")
    key = str(root.resolve())
    lock = index_lock._THREAD_LOCKS.setdefault(key, threading.RLock())
    held = threading.Event()
    release = threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        assert on_demand.ensure_indexed(root, root / "late.py") == "busy"
    finally:
        release.set()
        t.join()


# --- budget -------------------------------------------------------------------


def _typical_module() -> str:
    """About 40 KB of ordinary Python: a few classes, methods with
    docstrings, imports and calls between them."""
    parts = ["import os\nimport json\nfrom pathlib import Path\n\n\n"]
    for c in range(8):
        parts.append(f'class Service{c}:\n    """Service number {c}."""\n\n')
        for m in range(12):
            parts.append(
                f"    def method_{m}(self, value: int) -> int:\n"
                f'        """Compute step {m} of service {c} from value."""\n'
                f"        total = helper_{m % 5}(value) + {m}\n"
                "        if total > 10:\n"
                "            total = json.loads(json.dumps(total))\n"
                f"        return self.method_{(m + 1) % 12}(total) if total < 0 else total\n\n"
            )
        parts.append("\n")
    for h in range(5):
        parts.append(
            f"def helper_{h}(value: int) -> int:\n"
            f'    """Helper {h}."""\n'
            f"    return int(os.environ.get('K{h}', '0')) + value\n\n\n"
        )
    text = "".join(parts)
    while len(text) < 40 * 1024:
        text += "# filler comment line to reach a typical module size\n"
    return text


def _best_of(n: int, fn) -> float:
    best = float("inf")
    for _ in range(n):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def test_on_demand_40kb_python_stays_within_budget(root, capsys):
    text = _typical_module()
    assert 40 * 1024 <= len(text.encode()) < 48 * 1024

    # Inside the owner: index a new 40 KB file (one fresh file per round).
    paths = iter(root / f"typical_{i}.py" for i in range(3))

    def owner_side():
        p = next(paths)
        p.write_text(text)
        assert on_demand.ensure_indexed(root, p) == "indexed"

    indexed = _best_of(3, owner_side)

    # In a hook without an owner: classify, then parse in memory.
    target = root / "typical_hook.py"
    target.write_text(text)

    def hook_side():
        out = on_demand.for_hook(root, target, 0)
        assert out is not None and out.rows

    parsed = _best_of(3, hook_side)
    # A shared CI runner is slower and noisier than a workstation: there the
    # timings are still reported, against a generous ceiling.
    ceiling = 1.5 if os.environ.get("CI") else on_demand.BUDGET_S
    with capsys.disabled():
        print(
            f"\n[on-demand 40 KB .py] owner index {indexed * 1000:.0f} ms, "
            f"hook parse {parsed * 1000:.0f} ms, ceiling {ceiling * 1000:.0f} ms"
        )
    assert indexed < ceiling
    assert parsed < ceiling
