# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh-codewrite reference picker: name tokenization across snake
#              and camel case, path confinement, the graph-plus-filesystem
#              ranking, the graph-unavailable filesystem fallback, and
#              explicit-reference validation.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codewrite")

from cgh_codewrite.picker import CodeWriteError, name_tokens, pick_reference


def test_name_tokens_drops_noise_and_makes_case_variants():
    toks = name_tokens("test_user_service")
    assert "user_service" in toks  # snake, noise 'test' dropped
    assert "UserService" in toks  # Pascal for a class match
    assert "user" in toks and "service" in toks
    assert "test" not in toks


def test_name_tokens_splits_camel_case():
    toks = name_tokens("orderHandler")
    assert "order" in toks and "handler" in toks
    assert "OrderHandler" in toks


def test_explicit_reference_is_validated(tmp_path):
    root = tmp_path
    (root / "ref.py").write_text("x = 1\n", encoding="utf-8")
    out = pick_reference(root, "new/thing.py", "ref.py")
    assert out["reference"] == "ref.py"
    assert out["reason"] == "given by the caller"


def test_explicit_reference_outside_repo_is_rejected(tmp_path):
    with pytest.raises(CodeWriteError):
        pick_reference(tmp_path, "x.py", "/etc/passwd")


def test_explicit_reference_must_exist(tmp_path):
    with pytest.raises(CodeWriteError):
        pick_reference(tmp_path, "x.py", "missing.py")


def test_target_outside_repo_is_rejected(tmp_path):
    with pytest.raises(CodeWriteError):
        pick_reference(tmp_path, "../../escape.py")


def test_picks_sibling_from_filesystem_when_graph_absent(tmp_path, monkeypatch):
    # No graph: find_symbol_files returns None. The picker must still find
    # the sibling test in the same directory.
    monkeypatch.setattr("codegraph.plugin_api.find_symbol_files", lambda root, q: None)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_order_service.py").write_text("# order\n", encoding="utf-8")
    out = pick_reference(tmp_path, "tests/test_user_service.py")
    assert out["reference"] == "tests/test_order_service.py"
    assert out["graph_available"] is False
    assert "sibling in the same directory" in out["reason"]


def test_graph_hit_outranks_a_bare_sibling(tmp_path, monkeypatch):
    # Two siblings of the same kind; only one defines a matching symbol.
    # The graph hit must win.
    src = tmp_path / "src"
    src.mkdir()
    winner = src / "user_service.py"
    winner.write_text("class UserService:\n    pass\n", encoding="utf-8")
    (src / "unrelated.py").write_text("x = 1\n", encoding="utf-8")

    def fake_find(root, q):
        # Report the winner for the Pascal/user tokens, nothing otherwise.
        if q in ("UserService", "user_service", "user", "User"):
            return [
                {"kind": "class", "name": "UserService", "file": str(winner), "line": 1}
            ]
        return []

    monkeypatch.setattr("codegraph.plugin_api.find_symbol_files", fake_find)
    out = pick_reference(tmp_path, "src/user_service_v2.py")
    assert out["reference"] == "src/user_service.py"
    assert "defines a matching symbol" in out["reason"]
    assert out["graph_available"] is True


def test_no_analogue_returns_none_reference(tmp_path, monkeypatch):
    monkeypatch.setattr("codegraph.plugin_api.find_symbol_files", lambda root, q: [])
    (tmp_path / "readme.txt").write_text("hi\n", encoding="utf-8")  # wrong suffix
    out = pick_reference(tmp_path, "src/brand_new.py")
    assert out["reference"] is None
    assert "no analogue" in out["reason"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
