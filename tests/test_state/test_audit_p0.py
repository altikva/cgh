# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The two P0 audit fixes. The secure-mode probe of
#              record_findings fails CLOSED: a broken guard_mode probe
#              pseudonymizes instead of silently writing raw PII. And
#              the add_directory MCP tool refuses paths outside the
#              repo root unless a human declared them in extra_dirs.

from __future__ import annotations

import re

import pytest

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


class TestProbeFailsClosed:
    def test_broken_probe_still_pseudonymizes(self, tmp_path, monkeypatch):
        (tmp_path / ".codegraph").mkdir()

        import codegraph.state.guard as guard

        def boom(repo_root):
            raise OSError("config unreadable")

        monkeypatch.setattr(guard, "guard_mode", boom)

        store.record_findings(
            tmp_path,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value="joy@altikva.com", severity="warn")],
        )
        rows = store.query_findings(tmp_path, key_prefix="pii.")
        assert re.match(r"^<pii\.email:[0-9a-f]{10}>$", rows[0]["value"])
        blob = store.findings_db_path(tmp_path).read_bytes()
        assert b"joy@altikva.com" not in blob

    def test_working_probe_in_assist_stays_raw(self, tmp_path):
        (tmp_path / ".codegraph").mkdir()
        (tmp_path / ".codegraph" / "config.toml").write_text(
            '[codegraph]\nmode = "assist"\n', encoding="utf-8"
        )
        store.record_findings(
            tmp_path,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value="joy@altikva.com", severity="warn")],
        )
        rows = store.query_findings(tmp_path, key_prefix="pii.")
        assert rows[0]["value"] == "joy@altikva.com"


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
