# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The architecture report: findings on a hand-built graph
#              (single-link bridges, bridge files, layer inversions, calls
#              with no import, interface dispatch left out, cycles across
#              subsystems), the rule-made questions, and the report over an
#              indexed repo on both backends: MCP tool, CLI with --out,
#              determinism, hotspot ranking from git history, federation.

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import arch_report as ar
from codegraph.analysis import communities as cm
from codegraph.analysis import federation
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from tests.test_analysis.test_communities import _FakeMcp, _write_two_package_repo

# ---------------------------------------------------------------------------
# Findings on a hand-built graph
# ---------------------------------------------------------------------------

A = [f"/r/app/billing/inv_{i}.py" for i in range(4)]
B = [f"/r/app/auth/tok_{i}.py" for i in range(4)]
C = [f"/r/app/web/page_{i}.py" for i in range(4)]


class _NoTests:
    """A graph connection where no test imports anything."""

    def find_neighbors(self, *a, **k):
        return []

    def name_refs_into(self, *a, **k):
        return []


def _graph() -> cm.FileGraph:
    imports: dict[tuple[str, str], int] = {}
    for grp in (A, B, C):
        for i, a in enumerate(grp):
            for b in grp[i + 1 :]:
                imports[(a, b)] = 1
    imports[(A[0], B[0])] = 1  # the only billing <-> auth link
    imports[(C[0], A[1])] = 1  # web reaches billing twice
    imports[(C[1], A[2])] = 1
    imports[(A[3], C[3])] = 1  # domain code importing presentation code
    calls = {(B[1], C[2]): 1}  # a call with no import behind it
    # One call site resolved into two files: an interface dispatch.
    calls[(B[2], C[0])] = 1
    calls[(B[2], C[1])] = 1
    sites = {
        (B[1], C[2]): (10, "login", "render"),
        (B[2], C[0]): (20, "run", "handle"),
        (B[2], C[1]): (20, "run", "handle"),
    }
    weights: dict[tuple[str, str], float] = {}
    for a, b in list(imports) + list(calls):
        key = (a, b) if a <= b else (b, a)
        inside = any(a in g and b in g for g in (A, B, C))
        weights[key] = weights.get(key, 0.0) + (3.0 if inside else 1.0)
    roles = {f: ("other", "domain") for f in A}
    roles.update({f: ("other", "presentation") for f in C})
    roles.update({f: ("other", "infra") for f in B})
    return cm.FileGraph(
        nodes=sorted({p for k in weights for p in k}),
        weights=weights,
        imports=imports,
        calls=calls,
        roles=roles,
        call_sites=sites,
    )


@pytest.fixture
def findings() -> ar.ScopeFindings:
    result = cm.detect_communities(_graph())
    cycles = [[C[3], A[3]]]
    return ar.collect_findings(_NoTests(), "parent", result, cycles)


def _cid(f: ar.ScopeFindings, path: str) -> int:
    return f.result.membership[path]


def test_three_subsystems(findings):
    groups = {frozenset(c.files) for c in findings.result.communities}
    assert groups == {frozenset(A), frozenset(B), frozenset(C)}


def test_only_link_between_two_subsystems_is_a_bridge(findings):
    sole = {(lk.src, lk.dst) for lk in findings.sole_links}
    assert (A[0], B[0]) in sole
    # billing and web share several links: not a single-link bridge.
    assert not any({lk.src, lk.dst} <= set(A) | set(C) for lk in findings.sole_links)


def test_layer_inversion_found(findings):
    assert [(lk.src, lk.dst) for lk in findings.inversions] == [(A[3], C[3])]


def test_call_without_import_found_but_not_interface_dispatch(findings):
    pairs = [(lk.src, lk.dst) for lk in findings.calls_only]
    assert pairs == [(B[1], C[2])]
    assert findings.calls_only[0].site == (10, "login", "render")


def test_pivots_by_in_degree_and_untested(findings):
    assert findings.pivots_ranked_by == "imports"
    counts = [p.importers for p in findings.pivots]
    assert counts == sorted(counts, reverse=True)
    assert findings.pivots[0].file == C[3]  # three in web, one from billing
    assert all(not p.tested for p in findings.pivots)


def test_cycle_across_subsystems(findings):
    assert len(findings.cycles[0].communities) == 2


def test_questions_follow_the_rules_in_order(findings):
    qs = ar.ask_questions([findings], lambda p: p)
    assert qs[0].startswith(f"`{A[0]}` -> `{B[0]}` is the only link")
    assert any("should that dependency point the other way" in q for q in qs)
    assert any("import cycle of 2 files" in q for q in qs)
    assert any("no test imports" in q for q in qs)
    assert any(f"`{B[1]}:10` calls `render()`" in q for q in qs)
    assert len(qs) <= ar.MAX_QUESTIONS


def test_render_is_capped_and_deterministic(findings):
    md1, _ = ar.render_report([findings], lambda p: p, max_items=1)
    md2, _ = ar.render_report([findings], lambda p: p, max_items=1)
    assert md1 == md2
    for section in (
        "## Subsystems",
        "## Pivot files",
        "## Bridges",
        "## Unexpected links",
        "## Import cycles",
        "## Questions worth asking",
    ):
        assert section in md1
    pivot_rows = [ln for ln in md1.splitlines() if ln.startswith("| `")]
    assert len(pivot_rows) == 1  # max_items=1
    assert f"`{B[1]}:10` `login()` calls `render()`" in md1


# ---------------------------------------------------------------------------
# Indexed repo, both backends
# ---------------------------------------------------------------------------


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
    # One link between the two packages, from a function body.
    (root / "pkg" / "alpha" / "ledger_0.py").write_text(
        "from pkg.util import shared\n"
        "from pkg.alpha.ledger_1 import f_alpha_1\n\n\n"
        "def f_alpha_0():\n"
        "    from pkg.beta.router_3 import f_beta_3\n\n"
        "    return shared() + f_alpha_1() + f_beta_3()\n"
    )
    index_repo(root, method="os_walk")
    _srv._root = root
    yield root
    reset_connection()
    cm.reset_for_tests()
    _srv._root = None


def _tool(name: str):
    from codegraph.server.tools_arch import register

    m = _FakeMcp()
    register(m)
    return m.tools[name]


def test_report_tool_over_an_indexed_repo(repo):
    out = json.loads(_tool("architecture_report")())
    md = out["markdown"]
    assert md.startswith("# Architecture report: repo")
    assert "alpha: ledger" in md and "beta: router" in md
    assert "`pkg/util.py`" in md  # the shared utility
    summary = out["scopes"][0]
    assert summary["scope"] == "parent" and summary["subsystems"] == 2
    assert summary["sole_links"] == 1
    assert out["questions"] and "only link" in out["questions"][0]
    assert "ledger_0.py" in out["questions"][0]


def test_report_is_deterministic(repo):
    tool = _tool("architecture_report")
    first = tool()
    cm.reset_for_tests()
    assert tool() == first


def test_cli_report_writes_out_file(repo, tmp_path, capsys):
    from codegraph.cli.commands_query import cmd_report

    target = tmp_path / "out" / "ARCH.md"
    args = argparse.Namespace(
        root=str(repo), limit=5, resolution=1.0, json=False, out=str(target)
    )
    cmd_report(args)
    printed = capsys.readouterr().out
    assert printed.startswith("# Architecture report")
    assert target.read_text(encoding="utf-8").strip() == printed.strip()


# ---------------------------------------------------------------------------
# Hotspots from git history, federation
# ---------------------------------------------------------------------------

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/usr/local/bin"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        env=_ENV,
    )


def test_pivots_come_from_hotspots_with_git_history(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    monkeypatch.setenv("CGH_DB", "sqlite")
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    reset_connection()
    cm.reset_for_tests()
    root = tmp_path.resolve() / "repo"
    _write_two_package_repo(root)
    (root / ".gitignore").write_text(".codegraph/\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    index_repo(root, method="os_walk")
    _srv._root = root
    try:
        out = json.loads(_tool("architecture_report")())
    finally:
        reset_connection()
        cm.reset_for_tests()
        _srv._root = None
    assert "Ranked by change-risk hotspots" in out["markdown"]


def test_federated_report_has_a_section_per_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
    monkeypatch.setenv("CGH_DB", "sqlite")
    federation.reset_for_tests()
    reset_connection()
    cm.reset_for_tests()
    parent = tmp_path.resolve() / "parent"
    child = parent / "api"
    _write_two_package_repo(child)
    (parent / "front").mkdir(parents=True)
    (parent / "front" / "__init__.py").write_text("")
    for i in range(3):
        (parent / "front" / f"view_{i}.py").write_text(
            f"from front.view_{(i + 1) % 3} import v{(i + 1) % 3}\n\n\n"
            f"def v{i}():\n    return 1\n"
        )
    index_repo(child, method="os_walk")
    reset_connection()
    (parent / ".codegraph").mkdir(exist_ok=True)
    (parent / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nsubrepos = ["./api"]\nfederate_auto_up = false\n'
    )
    index_repo(parent, method="os_walk")
    _srv._root = parent
    try:
        out = json.loads(_tool("architecture_report")())
    finally:
        reset_connection()
        _srv._root = None
        federation.reset_for_tests()
        cm.reset_for_tests()
    md = out["markdown"]
    assert "## Scope `parent`" in md and "## Scope `api`" in md
    assert {s["scope"] for s in out["scopes"]} == {"parent", "api"}
