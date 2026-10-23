# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Subsystem detection: the Louvain core on hand-built graphs
#              (separation, determinism, resolution, connected parts), hub
#              exclusion, naming from content, the index-state cache, and
#              the communities MCP tool and CLI over an indexed repo on both
#              graph backends, federated scopes included.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import communities as cm
from codegraph.analysis import federation
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, index_repo

# ---------------------------------------------------------------------------
# Louvain on hand-built graphs
# ---------------------------------------------------------------------------


def _clique(nodes: list[int], w: float = 1.0) -> dict[tuple[int, int], float]:
    return {(a, b): w for i, a in enumerate(nodes) for b in nodes[i + 1 :]}


def _cliques_ring(k: int, size: int) -> dict[tuple[int, int], float]:
    """``k`` cliques of ``size`` nodes, each tied to the next by one edge."""
    edges: dict[tuple[int, int], float] = {}
    for c in range(k):
        nodes = list(range(c * size, (c + 1) * size))
        edges.update(_clique(nodes))
        edges[(nodes[-1], ((c + 1) % k) * size)] = 1.0
    return edges


def _groups(labels: list[int]) -> set[frozenset[int]]:
    out: dict[int, set[int]] = {}
    for i, c in enumerate(labels):
        out.setdefault(c, set()).add(i)
    return {frozenset(v) for v in out.values()}


def test_two_cliques_joined_by_one_edge_split_in_two():
    edges = {**_clique([0, 1, 2, 3]), **_clique([4, 5, 6, 7]), (3, 4): 1.0}
    labels = cm.louvain(8, edges)
    assert _groups(labels) == {frozenset({0, 1, 2, 3}), frozenset({4, 5, 6, 7})}


def test_same_graph_same_partition_whatever_the_edge_order():
    edges = _cliques_ring(6, 5)
    first = cm.louvain(30, edges)
    items = list(edges.items())
    for order in (items[::-1], items[7:] + items[:7], items[1::2] + items[::2]):
        assert cm.louvain(30, dict(order)) == first
    assert len(set(first)) == 6


def test_resolution_trades_size_for_count():
    edges = _cliques_ring(12, 4)
    coarse = len(set(cm.louvain(48, edges, resolution=0.05)))
    default = len(set(cm.louvain(48, edges, resolution=1.0)))
    fine = len(set(cm.louvain(48, edges, resolution=3.0)))
    assert coarse < default <= fine
    assert default == 12


def test_empty_and_edgeless_graphs_stay_singletons():
    assert cm.louvain(0, {}) == []
    assert cm.louvain(3, {}) == [0, 1, 2]


def test_split_disconnected_parts():
    adj: list[dict[int, float]] = [{1: 1.0}, {0: 1.0}, {3: 1.0}, {2: 1.0}]
    assert _groups(cm.split_disconnected([0, 0, 0, 0], adj)) == {
        frozenset({0, 1}),
        frozenset({2, 3}),
    }


def test_modularity_of_a_good_split_beats_one_block():
    edges = {**_clique([0, 1, 2]), **_clique([3, 4, 5]), (2, 3): 1.0}
    adj: list[dict[int, float]] = [{} for _ in range(6)]
    for (a, b), w in edges.items():
        adj[a][b] = w
        adj[b][a] = w
    assert cm.modularity([0, 0, 0, 1, 1, 1], adj) > cm.modularity([0] * 6, adj)


# ---------------------------------------------------------------------------
# Hubs and naming
# ---------------------------------------------------------------------------


def test_hub_needs_both_the_percentile_and_the_floor():
    degree = {f"f{i}": 2 for i in range(200)}
    degree["util"] = 40
    degree["busy"] = 9
    assert cm.find_hubs(degree) == {"util"}
    # Below the floor, even the most linked file stays in.
    assert cm.find_hubs({"a": 3, "b": 1, "c": 1}) == set()


def _graph(weights: dict[tuple[str, str], float]) -> cm.FileGraph:
    keyed = {tuple(sorted(k)): w for k, w in weights.items()}
    return cm.FileGraph(nodes=sorted({p for k in keyed for p in k}), weights=keyed)


def test_hub_left_out_so_it_does_not_glue_subsystems():
    weights: dict[tuple[str, str], float] = {}
    alpha = [f"/r/pkg/alpha/a{i}.py" for i in range(6)]
    beta = [f"/r/pkg/beta/b{i}.py" for i in range(6)]
    for group in (alpha, beta):
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                weights[(a, b)] = 1.0
    for f in alpha + beta:
        weights[(f, "/r/pkg/util.py")] = 1.0
    result = cm.detect_communities(_graph(weights))
    assert [h.file for h in result.hubs] == ["/r/pkg/util.py"]
    assert result.hubs[0].communities == 2
    assert "/r/pkg/util.py" not in result.membership
    assert {frozenset(c.files) for c in result.communities} == {
        frozenset(alpha),
        frozenset(beta),
    }
    # With a floor too high to reach, the hub is clustered like any file.
    kept = cm.detect_communities(_graph(weights), hub_min_degree=100)
    assert kept.hubs == []
    assert "/r/pkg/util.py" in kept.membership


def test_names_come_from_dominant_dir_and_recurring_words():
    files = [
        "/r/app/billing/invoice_store.py",
        "/r/app/billing/invoice_render.py",
        "/r/app/billing/tax.py",
    ]
    others = ["/r/app/auth/login.py", "/r/app/auth/token.py"]
    namer = cm._Namer.for_groups([files, others])
    # The package root ("app") holds every file and is left out of labels.
    assert namer.name(files) == "billing: invoice"
    # No recurring word: the central (first) file's stem stands in.
    assert namer.name(others) == "auth: login"


def test_name_spread_over_two_dirs_joins_them():
    files = [
        "/r/app/cli/run.py",
        "/r/app/cli/show.py",
        "/r/app/server/run_tools.py",
        "/r/app/server/show_tools.py",
    ]
    namer = cm._Namer.for_groups([files])
    assert namer.name(files).startswith("cli+server")


def test_name_tokens_split_case_and_drop_noise():
    assert cm.name_tokens("InvoiceStore_v2") == ["invoice", "store"]
    assert cm.name_tokens("__init__") == []


def test_duplicate_names_are_told_apart():
    weights: dict[tuple[str, str], float] = {}
    for grp in ("one", "two"):
        files = [f"/r/pkg/{grp}/x/item_{i}.py" for i in range(3)]
        for i, a in enumerate(files):
            for b in files[i + 1 :]:
                weights[(a, b)] = 1.0
    result = cm.detect_communities(_graph(weights))
    names = [c.name for c in result.communities]
    assert len(set(names)) == len(names) == 2


# ---------------------------------------------------------------------------
# Indexed repo, both backends
# ---------------------------------------------------------------------------


def _write_two_package_repo(root: Path) -> None:
    """Two packages that only talk inside themselves, one utility module
    every file imports, and a test file importing both packages."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "util.py").write_text("def shared():\n    return 1\n")
    for grp, prefix in (("alpha", "ledger"), ("beta", "router")):
        d = root / "pkg" / grp
        d.mkdir()
        (d / "__init__.py").write_text("")
        for i in range(6):
            nxt = (i + 1) % 6
            (d / f"{prefix}_{i}.py").write_text(
                "from pkg.util import shared\n"
                f"from pkg.{grp}.{prefix}_{nxt} import f_{grp}_{nxt}\n\n\n"
                f"def f_{grp}_{i}():\n"
                f"    return shared() + f_{grp}_{nxt}()\n"
            )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_all.py").write_text(
        "from pkg.alpha.ledger_0 import f_alpha_0\n"
        "from pkg.beta.router_0 import f_beta_0\n\n\n"
        "def test_both():\n    assert f_alpha_0() and f_beta_0()\n"
    )


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    cm.reset_for_tests()
    root = tmp_path.resolve() / "repo"
    _write_two_package_repo(root)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._fts_conn = None
    yield root
    reset_connection()
    cm.reset_for_tests()
    _srv._root = None
    _srv._fts_conn = None


def _tools() -> dict:
    from codegraph.server.tools_arch import register

    m = _FakeMcp()
    register(m)
    return m.tools


def test_tool_finds_both_packages_and_sets_the_utility_aside(repo):
    out = json.loads(_tools()["communities"]())
    names = sorted(c["name"] for c in out["communities"])
    assert len(names) == 2
    assert any(n.startswith("alpha") and "ledger" in n for n in names), names
    assert any(n.startswith("beta") and "router" in n for n in names), names
    for c in out["communities"]:
        assert c["scope"] == "parent"
        assert c["size"] == 6
        assert c["cohesion"] == 1.0
        assert all(not f.startswith("tests") for f in c["files"])
        assert c["top_symbols"] and {"name", "file", "line", "callers"} <= set(
            c["top_symbols"][0]
        )
    hubs = [h["file"] for h in out["shared_utilities"]]
    assert hubs == [str(Path("pkg") / "util.py")]
    assert out["scopes"][0]["shared_utilities"] == 1


def test_tool_answer_is_deterministic(repo):
    tools = _tools()
    first = tools["communities"]()
    cm.reset_for_tests()
    assert tools["communities"]() == first


def test_min_size_and_cap(repo):
    tools = _tools()
    assert json.loads(tools["communities"](min_size=7))["count"] == 0
    capped = json.loads(tools["communities"](max_communities=1))
    assert capped["count"] == 1 and capped["truncated"] is True


def test_cache_hit_then_invalidated_by_a_reindex(repo):
    conn = get_connection(repo)
    first = cm.communities_for(conn)
    assert cm.communities_for(conn) is first
    target = repo / "pkg" / "alpha" / "ledger_3.py"
    target.write_text(target.read_text() + "\n\ndef extra():\n    return 2\n")
    index_file(target, repo)
    assert cm.communities_for(conn) is not first
    # A different resolution is its own entry.
    assert cm.communities_for(conn, 2.0) is not cm.communities_for(conn, 1.0)


def test_architecture_overview_lists_subsystems(repo):
    out = json.loads(_tools()["architecture_overview"]())
    subs = out["subsystems"]
    assert len(subs) == 2
    assert {"name", "size", "central"} <= set(subs[0])


def test_cli_prints_the_table_and_json(repo, capsys):
    from codegraph.cli.commands_query import cmd_communities

    args = argparse.Namespace(
        root=str(repo), resolution=1.0, min_size=2, limit=30, json=True
    )
    cmd_communities(args)
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 2
    args.json = False
    cmd_communities(args)
    text = capsys.readouterr().out
    assert "cohesion" in text and "util.py" in text


# ---------------------------------------------------------------------------
# Federation: each scope clustered on its own graph
# ---------------------------------------------------------------------------


@pytest.fixture
def fed(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    federation.reset_for_tests()
    reset_connection()
    cm.reset_for_tests()
    parent = tmp_path.resolve() / "parent"
    child = parent / "api"
    _write_two_package_repo(child)
    (parent / "front").mkdir(parents=True)
    for i in range(3):
        (parent / "front" / f"view_{i}.py").write_text(
            f"from front.view_{(i + 1) % 3} import v{(i + 1) % 3}\n\n\n"
            f"def v{i}():\n    return 1\n"
        )
    (parent / "front" / "__init__.py").write_text("")
    index_repo(child, method="os_walk")
    reset_connection()
    (parent / ".codegraph").mkdir(exist_ok=True)
    (parent / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nsubrepos = ["./api"]\nfederate_auto_up = false\n'
    )
    index_repo(parent, method="os_walk")
    _srv._root = parent
    yield parent
    reset_connection()
    _srv._root = None
    federation.reset_for_tests()
    cm.reset_for_tests()


def test_federated_scopes_are_clustered_apart(fed):
    out = json.loads(_tools()["communities"]())
    by_scope: dict[str, list[str]] = {}
    for c in out["communities"]:
        by_scope.setdefault(c["scope"], []).append(c["name"])
    assert set(by_scope) == {"parent", "api"}
    assert len(by_scope["api"]) == 2
    assert len(by_scope["parent"]) == 1
    assert {s["scope"] for s in out["scopes"]} == {"parent", "api"}
    assert all(h["scope"] == "api" for h in out["shared_utilities"])


def test_context_for_task_names_the_subsystem_of_its_matches(repo):
    from codegraph.server.tools_meta import register

    m = _FakeMcp()
    register(m)
    out = json.loads(m.tools["context_for_task"]("f_alpha_2 ledger"))
    sub = out["subsystem"]
    assert sub["name"].startswith("alpha") and sub["size"] == 6
    assert "**Subsystem**" in out["context_markdown"]
    assert not set(sub["files"]) & set(out["start_here"] + out["related"])


def test_function_body_imports_link_files(repo):
    lazy = repo / "pkg" / "lazy.py"
    lazy.write_text(
        "def run():\n"
        "    from pkg.beta.router_2 import f_beta_2\n\n"
        "    return f_beta_2()\n"
    )
    index_file(lazy, repo)
    graph = cm.build_file_graph(get_connection(repo))
    target = str(repo / "pkg" / "beta" / "router_2.py")
    assert (str(lazy), target) in graph.imports
