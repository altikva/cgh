# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the test-to-code mapping MCP tools (tests_for /
#              untested). Builds a tiny indexed repo with a source file and a
#              test_*.py that imports it (so roles.classify tags the test
#              file `test`), then asserts tests_for finds the test and
#              untested lists an un-imported source file.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_tests import register as register_tests


class _FakeMcp:
    """Minimal stand-in for FastMCP: .tool() records the decorated function
    unchanged so tests can call the tool bodies directly."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def mapping_root(tmp_path):
    reset_connection()
    _srv._root = tmp_path.resolve()
    _srv._conn = None

    yield tmp_path.resolve()

    reset_connection()
    _srv._root = None
    _srv._conn = None


def _register() -> dict:
    m = _FakeMcp()
    register_tests(m)
    return m.tools


def _build_repo(root):
    """A tested module (mymod), its importing test, and an untested module."""
    (root / "mymod.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    (root / "test_mymod.py").write_text(
        "import mymod\n\n\ndef test_widget():\n    assert mymod.widget() == 1\n",
        encoding="utf-8",
    )
    (root / "lonely.py").write_text("def orphan():\n    return 2\n", encoding="utf-8")
    index_file(root / "mymod.py", root)
    index_file(root / "test_mymod.py", root)
    index_file(root / "lonely.py", root)


def test_tests_for_finds_importing_test(mapping_root):
    root = mapping_root
    _build_repo(root)

    tools = _register()
    out = json.loads(tools["tests_for"](str(root / "mymod.py")))

    assert out["count"] >= 1
    files = {t["file"] for t in out["tests"]}
    assert any(f.endswith("test_mymod.py") for f in files)
    # Every reported test carries the `test` role.
    assert all(t["role"] == "test" for t in out["tests"])
    assert "note" in out


def test_tests_for_accepts_relative_path(mapping_root):
    root = mapping_root
    _build_repo(root)

    tools = _register()
    out = json.loads(tools["tests_for"]("mymod.py"))
    files = {t["file"] for t in out["tests"]}
    assert any(f.endswith("test_mymod.py") for f in files)


def test_tests_for_by_symbol_name(mapping_root):
    root = mapping_root
    _build_repo(root)

    tools = _register()
    out = json.loads(tools["tests_for"]("widget"))
    files = {t["file"] for t in out["tests"]}
    assert any(f.endswith("test_mymod.py") for f in files)


def test_untested_lists_unimported_source(mapping_root):
    root = mapping_root
    _build_repo(root)

    tools = _register()
    out = json.loads(tools["untested"]())

    files = {u["file"] for u in out["untested"]}
    # lonely.py has no importing test -> untested.
    assert any(f.endswith("lonely.py") for f in files)
    # mymod.py IS imported by a test -> not untested.
    assert not any(f.endswith("mymod.py") for f in files)
    # Test files are never reported as untested.
    assert not any(f.endswith("test_mymod.py") for f in files)
    assert "note" in out
