# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: find_symbol_files, the read-only graph query exposed to
#              plugins. Covers a match on an indexed repo, the empty-result
#              vs None distinction (readable-but-empty vs graph-unavailable),
#              and that the function is reachable through the public
#              plugin_api re-export surface.

from __future__ import annotations

import pytest

from codegraph.analysis.plugin_queries import find_symbol_files
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "user_service.py").write_text(
        "class UserService:\n    def create(self):\n        return 1\n",
        encoding="utf-8",
    )
    (root / "helpers.py").write_text(
        "def user_helper():\n    return 2\n",
        encoding="utf-8",
    )
    reset_connection()
    index_repo(str(root))
    reset_connection()
    yield root
    reset_connection()


def test_finds_class_by_substring(indexed):
    rows = find_symbol_files(str(indexed), "UserService")
    assert rows is not None
    files = {r["file"] for r in rows}
    assert any(f.endswith("user_service.py") for f in files)
    assert any(r["kind"] == "class" and r["name"] == "UserService" for r in rows)


def test_substring_match_is_case_sensitive(indexed):
    # Matches search_symbols: contains is a case-sensitive substring. A
    # lowercase stem finds the snake_case function but not the PascalCase
    # class; the reference picker generates case variants to bridge this.
    lower = find_symbol_files(str(indexed), "user")
    assert lower is not None
    assert {r["name"] for r in lower} == {"user_helper"}

    upper = find_symbol_files(str(indexed), "User")
    assert upper is not None
    assert "UserService" in {r["name"] for r in upper}


def test_readable_but_no_match_returns_empty_list(indexed):
    rows = find_symbol_files(str(indexed), "NoSuchSymbolAnywhere")
    assert rows == []  # empty list, not None: the graph was readable


def test_blank_query_returns_empty(indexed):
    assert find_symbol_files(str(indexed), "   ") == []


def test_unindexed_repo_returns_none(tmp_path):
    # No index built: the graph cannot be read, so the caller is told to
    # fall back rather than handed a misleading empty result.
    reset_connection()
    assert find_symbol_files(str(tmp_path / "nope"), "anything") is None


def test_exposed_through_public_plugin_api():
    import codegraph.plugin_api as api

    assert api.find_symbol_files is find_symbol_files
    assert "find_symbol_files" in dir(api)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
