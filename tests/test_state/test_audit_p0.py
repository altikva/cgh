# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Findings are stored as scanners report them, whatever
#              the config says (pseudonymization at rest ended with
#              secure mode in 0.15.0), and pseudonyms written before
#              read back untouched. The add_directory MCP tool refuses
#              paths outside the repo root unless a human declared them
#              in extra_dirs.

from __future__ import annotations

import pytest

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


class TestFindingsStoredAsIs:
    def test_legacy_secure_config_stores_raw_values(self, tmp_path):
        (tmp_path / ".codegraph").mkdir()
        (tmp_path / ".codegraph" / "config.toml").write_text(
            '[codegraph]\nmode = "secure"\n', encoding="utf-8"
        )
        store.record_findings(
            tmp_path,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value="joy@altikva.com", severity="warn")],
        )
        rows = store.query_findings(tmp_path, key_prefix="pii.")
        assert rows[0]["value"] == "joy@altikva.com"
        assert not (tmp_path / ".codegraph" / "pseudo.key").exists()

    def test_existing_pseudonyms_are_left_alone(self, tmp_path):
        (tmp_path / ".codegraph").mkdir()
        old = "<pii.email:3fa2b4c5d6>"
        store.record_findings(
            tmp_path,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value=old, severity="warn")],
        )
        rows = store.query_findings(tmp_path, key_prefix="pii.")
        assert rows[0]["value"] == old


class TestAddDirectoryContainment:
    """Exercise the containment logic through the registered MCP tool."""

    def _register(self, root):
        import codegraph.server as srv
        from codegraph.server.tools_index import register as register_tools

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *a, **k):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return deco

        old_root = srv._root
        srv._root = root
        mcp = FakeMCP()
        register_tools(mcp)
        return mcp, old_root

    def _init_repo(self, root):
        (root / ".codegraph").mkdir(parents=True, exist_ok=True)
        (root / ".codegraph" / "config.toml").write_text(
            "[codegraph]\n", encoding="utf-8"
        )

    def test_outside_root_undeclared_is_refused(self, tmp_path):
        import json

        repo = tmp_path / "repo"
        outside = tmp_path / "loot"
        repo.mkdir()
        outside.mkdir()
        (outside / "secrets.py").write_text("x = 1\n", encoding="utf-8")
        self._init_repo(repo)

        mcp, old_root = self._register(repo)
        try:
            out = json.loads(mcp.tools["add_directory"](str(outside)))
        finally:
            import codegraph.server as srv

            srv._root = old_root
        assert out["status"] == "error"
        assert "extra_dirs" in out["message"]

    def test_outside_root_declared_is_allowed(self, tmp_path):
        import json

        repo = tmp_path / "repo"
        sibling = tmp_path / "frontend"
        repo.mkdir()
        sibling.mkdir()
        (sibling / "app.py").write_text("x = 1\n", encoding="utf-8")
        (repo / ".codegraph").mkdir()
        (repo / ".codegraph" / "config.toml").write_text(
            f'[codegraph]\nextra_dirs = ["{sibling.resolve()}"]\n', encoding="utf-8"
        )

        mcp, old_root = self._register(repo)
        try:
            out = json.loads(mcp.tools["add_directory"](str(sibling)))
        finally:
            import codegraph.server as srv

            srv._root = old_root
        assert out["status"] != "error"

    def test_inside_root_is_always_allowed(self, tmp_path):
        import json

        repo = tmp_path / "repo"
        inner = repo / "docs"
        inner.mkdir(parents=True)
        (inner / "note.md").write_text("# hi\n", encoding="utf-8")
        self._init_repo(repo)

        mcp, old_root = self._register(repo)
        try:
            out = json.loads(mcp.tools["add_directory"]("docs"))
        finally:
            import codegraph.server as srv

            srv._root = old_root
        assert out["status"] != "error"
