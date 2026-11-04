# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: context_for_task answers an agent can act on without reading
#              whole files: repo-relative paths (relative to each subrepo's
#              root in a federated workspace), a line range and justification
#              lines per symbol, the files split into start_here (three at
#              most) and related (five at most), a next step, and memory,
#              plans and knowledge as one-line pointers. The tiering and next-step
#              helpers are also tested on their own.

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import federation
from codegraph.analysis.context_builder import (
    RELATED_MAX,
    START_HERE_MAX,
    WHY_MAX_CHARS,
    WHY_MAX_LINES,
    ContextNode,
    KnowledgeHit,
    MemoryDocHit,
    PlanDocHit,
    TaskContext,
    _local_memory_hits,
    file_tiers,
    next_step,
    render_context_markdown,
)
from codegraph.core.db import reset_connection
from codegraph.core.fts import MemoryHit
from codegraph.indexer import index_repo
from codegraph.server._budget import CONTEXT_MAX_BYTES


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server import _register_meta

    m = _FakeMcp()
    _register_meta(m)
    return m.tools


_LEDGER = '''\
def post_ledger_entry(account, amount):
    """Record one ledger entry. The ledger balance is updated in place."""
    return amount


def close_ledger_period(period):
    """Close the ledger period and freeze its entries."""
    total = post_ledger_entry(period, 0)
    return total


class LedgerReport:
    def render(self):
        return "ledger report"
'''

_API = '''\
def ledger_api_handler(request):
    """HTTP handler that exposes the ledger to clients."""
    return request
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    federation.reset_for_tests()
    reset_connection()
    root = tmp_path.resolve() / "parent"
    (root / "books").mkdir(parents=True)
    (root / "books" / "ledger.py").write_text(_LEDGER)
    (root / "tests").mkdir()
    (root / "tests" / "test_ledger.py").write_text(
        "from books.ledger import close_ledger_period\n\n\n"
        "def test_close_ledger_period():\n"
        "    assert close_ledger_period(1) == 0\n"
    )
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None
    federation.reset_for_tests()


@pytest.fixture
def fed(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    federation.reset_for_tests()
    reset_connection()
    parent = tmp_path.resolve() / "parent"
    child = parent / "api"
    (child / "web").mkdir(parents=True)
    (child / "web" / "handlers.py").write_text(_API)
    index_repo(child, method="os_walk")
    reset_connection()
    (parent / "books").mkdir()
    (parent / "books" / "ledger.py").write_text(_LEDGER)
    (parent / ".codegraph").mkdir(exist_ok=True)
    (parent / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nsubrepos = ["./api"]\nfederate_auto_up = false\n'
    )
    index_repo(parent, method="os_walk")
    _srv._root = parent
    _srv._conn = None
    _srv._fts_conn = None
    yield parent, child
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None
    federation.reset_for_tests()


_HEADER = re.compile(
    r"^\[\w+\] `[^`]+`(?: \((?P<scope>[\w.-]+)\))? (?P<path>\S+):(?P<lines>\d+(?:-\d+)?)$"
)


def _blocks(out: dict) -> list[tuple[re.Match, list[str]]]:
    """Each symbol of the markdown: its parsed header and its why lines."""
    found = []
    symbols = out["context_markdown"].split("\n## See also", 1)[0]
    for block in symbols.split("\n### ")[1:]:
        head, *rest = block.split("\n")
        m = _HEADER.match(head)
        assert m, head
        found.append((m, [ln[2:] for ln in rest if ln.startswith("- ")]))
    return found


def test_paths_are_relative_to_the_repo(repo):
    raw = _tools()["context_for_task"](task="close the ledger period")
    assert str(repo) not in raw
    out = json.loads(raw)
    files = out["start_here"] + out["related"]
    assert "books/ledger.py" in files
    assert all(not Path(f).is_absolute() for f in files)
    assert all(not Path(m["path"]).is_absolute() for m, _ in _blocks(out))


def test_each_symbol_has_a_range_and_a_justification(repo):
    out = json.loads(_tools()["context_for_task"](task="close the ledger period"))
    blocks = _blocks(out)
    assert blocks
    for m, why in blocks:
        assert "-" in m["lines"], m.group(0)
        assert 1 <= len(why) <= WHY_MAX_LINES, m.group(0)
        assert all(len(w) <= WHY_MAX_CHARS for w in why)
    by_name = {m.group(0).split("`")[1]: why for m, why in blocks}
    # The docstring sentence that matches the task, not merely the first one.
    assert (
        "doc: Close the ledger period and freeze its entries."
        in by_name["close_ledger_period"]
    )
    # A call site, as path:line and the calling code.
    assert any(
        w.startswith("call: books/ledger.py:8 ") and "post_ledger_entry(" in w
        for w in by_name["post_ledger_entry"]
    )


def test_tiers_and_next_step(repo):
    out = json.loads(_tools()["context_for_task"](task="close the ledger period"))
    assert 1 <= len(out["start_here"]) <= START_HERE_MAX
    assert not set(out["start_here"]) & set(out["related"])
    # The code leads, the test follows.
    assert out["start_here"][0] == "books/ledger.py"
    assert re.fullmatch(r"Read books/ledger\.py offset=\d+ limit=\d+", out["next"])


def test_next_step_without_a_match_is_a_search(repo):
    out = json.loads(_tools()["context_for_task"](task="zzqx unmatched wording"))
    assert out["symbol_count"] == 0
    assert out["next"].startswith("fts_search(")


def test_federated_child_paths_are_relative_to_the_child(fed):
    parent, child = fed
    raw = _tools()["context_for_task"](task="ledger handler for clients")
    assert str(parent) not in raw and str(child) not in raw
    out = json.loads(raw)
    scoped = [(m, why) for m, why in _blocks(out) if m["scope"] == "api"]
    assert scoped, out["context_markdown"]
    assert scoped[0][0]["path"] == "web/handlers.py"
    assert scoped[0][1]
    files = out["start_here"] + out["related"]
    assert "api:web/handlers.py" in files
    assert "books/ledger.py" in files
    assert "partial" not in out


def test_next_step_reads_a_child_file_from_the_parent(tmp_path):
    root = tmp_path / "parent"
    node = ContextNode(
        kind="function",
        name="h",
        file_path=str(root / "api" / "web" / "h.py"),
        start_line=10,
        end_line=200,
        docstring="",
        relevance=1.0,
        scope="api",
        scope_root=str(root / "api"),
    )
    start, _ = file_tiers([node])
    assert start == ["api:web/h.py"]
    assert next_step([node], start, "x", root) == (
        "Read api/web/h.py offset=10 limit=80"
    )


def test_start_here_holds_at_most_three_files(tmp_path):
    nodes = [
        ContextNode(
            kind="function",
            name=f"f{i}",
            file_path=str(tmp_path / f"m{i}.py"),
            start_line=1,
            end_line=5,
            docstring="",
            relevance=1.0 - i / 10,
            scope_root=str(tmp_path),
        )
        for i in range(7)
    ]
    start, related = file_tiers(nodes)
    assert start == ["m0.py", "m1.py", "m2.py"]
    assert related == [f"m{i}.py" for i in range(3, 7)]


def test_related_holds_at_most_five_files(tmp_path):
    nodes = [
        ContextNode(
            kind="function",
            name=f"f{i:02}",
            file_path=str(tmp_path / f"m{i:02}.py"),
            start_line=1,
            end_line=5,
            docstring="",
            relevance=1.0 - i / 20,
            scope_root=str(tmp_path),
        )
        for i in range(12)
    ]
    start, related = file_tiers(nodes)
    assert len(start) == START_HERE_MAX
    assert related == [f"m{i:02}.py" for i in range(3, 3 + RELATED_MAX)]


def test_memory_plans_and_knowledge_are_one_line_pointers():
    body = "The long body of the entry that the answer must not carry. " * 5
    ctx = TaskContext(
        task="t",
        nodes=[],
        files_referenced=[],
        token_estimate=0,
        memory_docs=[
            MemoryDocHit(
                path="/m/feedback_x.md",
                kind="feedback",
                title="scan-meta-drift",
                snippet=body,
                score=1.0,
            )
        ],
        plan_docs=[
            PlanDocHit(
                path="/p/plan.md",
                slug="plan",
                agent_id="",
                title="Refactor the store",
                snippet=body,
                score=1.0,
            )
        ],
        knowledge_docs=[
            KnowledgeHit(
                id=42,
                kind="gotcha",
                title="Owner lock " + "x" * 200,
                body=body,
                tags=["papercut"],
                score=1.0,
            )
        ],
    )
    md = render_context_markdown(ctx)
    assert "long body" not in md and "papercut" not in md
    pointers = md.split("## See also", 1)[1].splitlines()[1:]
    assert pointers == [
        "- memory [feedback] scan-meta-drift",
        "- plan Refactor the store (/p/plan.md)",
        pointers[2],
    ]
    assert pointers[2].startswith("- knowledge [gotcha] Owner lock x")
    assert pointers[2].endswith("... (#42)")


def test_the_memory_index_file_is_not_a_hit(monkeypatch):
    hits = [
        MemoryHit("/m/MEMORY.md", "other", "Memory", "index", 2.0),
        MemoryHit("/m/feedback_a.md", "feedback", "a", "", 1.0),
    ]
    monkeypatch.setattr("codegraph.core.fts.memory_search", lambda *a, **k: hits)
    found = _local_memory_hits(None, "anything", limit=3)
    assert [m.path for m in found] == ["/m/feedback_a.md"]


# ---------------------------------------------------------------------------
# Under the cap on a large repo, and the session record
# ---------------------------------------------------------------------------

_N = 300


@pytest.fixture
def big(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    reset_connection()
    root = tmp_path.resolve()
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(_N):
        (pkg / f"billing_module_with_a_long_name_{i}.py").write_text(
            f"def compute_billing_invoice_total_{i}(order):\n"
            f'    """Compute the billing invoice total for order kind {i}. '
            + "It sums the lines of the invoice and applies the billing rules. " * 3
            + '"""\n'
            + "    return order\n\n\n"
            f"def run_billing_{i}(order):\n"
            f"    return compute_billing_invoice_total_{i}(order)\n"
        )
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None


def test_answer_stays_under_the_cap_on_a_large_repo(big):
    tools = _tools()
    task = "compute the billing invoice total"
    raw = tools["context_for_task"](task=task, max_nodes=200)
    assert len(raw.encode("utf-8")) <= CONTEXT_MAX_BYTES
    out = json.loads(raw)
    assert out["truncated"] is True and out["dropped"] > 0
    assert out["next"].startswith("Read pkg/")
    assert all(why for _m, why in _blocks(out))


def test_trimmed_symbols_come_back_with_the_same_session(big):
    tools = _tools()
    task = "compute the billing invoice total"
    first = json.loads(
        tools["context_for_task"](task=task, max_nodes=200, session_id="s")
    )
    assert first["truncated"] is True
    shown = {m.group(0) for m, _ in _blocks(first)}
    second = json.loads(
        tools["context_for_task"](task=task, max_nodes=200, session_id="s")
    )
    again = {m.group(0) for m, _ in _blocks(second)}
    assert again and not shown & again
    assert second["next"].startswith("Read pkg/")
