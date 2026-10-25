# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search's answer ceiling measured on the string the
#              tool returns and on the escaped form the agent receives, for
#              regex and glob searches over long and non-ASCII lines, a
#              federated workspace (the `scopes` note once re-serialized the
#              answer indented and ASCII-escaped, past 4 KB), count_only, a
#              search at a git ref and a long pattern.

from __future__ import annotations

import json
import subprocess

import pytest

import codegraph.server as _srv
from codegraph.analysis.federation import reset_for_tests
from codegraph.analysis.pattern import PATTERN_ANSWER_BYTES
from codegraph.analysis.query_intent import clear_centrality_cache
from codegraph.core.db import reset_connection
from codegraph.server._budget import wire_bytes
from codegraph.server.tools_query import register as register_query

_LINES = [
    "    value = compute_total(order, taxes)  # total de la commande",
    "    raise ValueError(f'montant invalide: {montant!r}, réessayez plus tard')",
    "    label = 'Prénom, nom, adresse électronique, numéro de téléphone'",
    "    note = '注文の合計金額を計算します ' * 6",
    "    payload = {'key': " + repr("x" * 380) + "}",
]


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _write_tree(root, n_dirs: int = 4, n_files: int = 6) -> None:
    for d in range(n_dirs):
        for f in range(n_files):
            path = root / f"service_{d}" / "handlers" / f"module_number_{f}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(_LINES[(i + f) % len(_LINES)] for i in range(12))
            path.write_text(body + "\n", encoding="utf-8")


def _git(root, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _setup(root, children: list) -> dict:
    reset_connection()
    reset_for_tests()
    _srv._root = root
    _srv._conn = None
    _srv._fts_conn = None
    clear_centrality_cache()
    m = _FakeMcp()
    register_query(m)
    return m.tools


@pytest.fixture
def plain(tmp_path) -> dict:
    root = tmp_path / "repo"
    _write_tree(root)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    yield _setup(root, [])
    _teardown()


@pytest.fixture
def federated(tmp_path) -> dict:
    root = tmp_path / "parent"
    _write_tree(root, n_dirs=2)
    children = []
    for name in ("billing-service-api", "customer-portal-web", "notifications"):
        child = tmp_path / name
        _write_tree(child, n_dirs=3)
        children.append(child)
    cfg = root / ".codegraph" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(json.dumps(str(c)) for c in children)
    cfg.write_text(f"[codegraph]\nsubrepos = [{listed}]\n", encoding="utf-8")
    yield _setup(root, children)
    _teardown()


def _teardown() -> None:
    reset_connection()
    reset_for_tests()
    _srv._root = None
    _srv._conn = None
    _srv._fts_conn = None
    clear_centrality_cache()


def _assert_capped(out: str) -> dict:
    assert len(out.encode("utf-8")) <= PATTERN_ANSWER_BYTES
    assert wire_bytes(out) <= PATTERN_ANSWER_BYTES
    return json.loads(out)


_SHAPES = [
    {"pattern": r"\b(?:value|raise|label|note|payload)\b", "glob": "*.py"},
    {"pattern": r"[^\x00-\x7f]", "glob": "*.py", "max_results": 200},
    {"pattern": r"é|ü|注", "glob": "*module_number_*.py"},
    {"pattern": r"x{50,}", "max_results": 100},
    {"pattern": "=", "regex": False, "max_results": 200},
]


@pytest.mark.parametrize("args", _SHAPES)
def test_answer_fits_the_cap(plain, args):
    out = _assert_capped(plain["pattern_search"](**args))
    assert out["truncated"] is True
    assert out["top_dirs"] and out["more"] and "count_only=True" in out["hint"]
    assert out["shown"] + sum(out["more"].values()) <= out["total"]


@pytest.mark.parametrize("args", _SHAPES)
def test_federated_answer_fits_the_cap(federated, args):
    out = _assert_capped(federated["pattern_search"](**args))
    assert out["top_dirs"] and out["more"] and out["hint"]
    assert "parent" in out["scopes"]
    shown = {g.get("scope", "parent") for g in out["files"]}
    assert shown <= set(out["scopes"])


def test_federated_answer_is_compact_and_keeps_accents(federated):
    """The regression: the `scopes` note was added by re-serializing the
    capped answer with indent=2 and ASCII escaping, which grew a 2.5 KB
    answer past 4 KB. The answer stays compact with its accents as is."""
    out = federated["pattern_search"](r"é", glob="*.py")
    assert not out.startswith("{\n")
    assert "\\u00e9" not in out and "é" in out
    _assert_capped(out)


def test_scopes_note_is_compact():
    payload = '{"a": "é"}'
    out = _srv._add_scopes(payload, {"parent": {"head": "abc"}})
    assert out == '{"a":"é","scopes":{"parent":{"head":"abc"}}}'


def test_count_only_fits_the_cap(federated):
    out = _assert_capped(federated["pattern_search"]("=", count_only=True))
    assert out["count_only"] is True and out["top_dirs"]


def test_search_at_ref_fits_the_cap(plain):
    out = _assert_capped(
        plain["pattern_search"](r"[^\x00-\x7f]", glob="*.py", ref="HEAD")
    )
    assert out["truncated"] is True and out["more"]


def test_long_pattern_fits_the_cap(plain):
    pattern = "|".join(f"name_{i}_" + "\\w+" for i in range(150)) + "|value"
    out = _assert_capped(plain["pattern_search"](pattern, glob="*.py"))
    assert out["total"] > 0


def test_sentence_fits_the_cap(federated):
    out = federated["pattern_search"]("où est calculé le total de la commande")
    _assert_capped(out)
