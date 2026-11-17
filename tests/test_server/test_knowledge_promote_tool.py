# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The knowledge_promote MCP tool (L9.3): promotes this repo's
#              learnings into the configured shared namespace by default, honours
#              an explicit target, entry_id selection and dry_run, and reports the
#              standing_instruction/glossary refusal.

from __future__ import annotations

import json

import pytest

import cgh.server as _srv
from cgh.server.tools_knowledge import register
from cgh.state.call_log import (
    _get_conn_at,
    reset_for_tests,
    shared_store_path,
)


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    reset_for_tests()
    root = tmp_path / "repo"
    (root / ".cgh").mkdir(parents=True)
    monkeypatch.setattr(_srv, "_root", root)
    m = _FakeMcp()
    register(m)
    yield m.tools, root
    reset_for_tests()


def _shared_titles(ns: str) -> list[str]:
    conn = _get_conn_at(shared_store_path(ns))
    return [r[0] for r in conn.execute("SELECT title FROM knowledge ORDER BY id")]


def test_tool_promotes_to_the_default_namespace(tools):
    t, _ = tools
    t["knowledge_record"](title="d", body="handlers return X", kind="decision")
    out = json.loads(t["knowledge_promote"]())
    assert out["promoted"] == 1
    assert out["target_namespace"] == "org-platform"
    assert _shared_titles("org-platform") == ["d"]


def test_tool_refuses_standing_instruction(tools):
    t, _ = tools
    t["knowledge_record"](title="rule", body="always X", kind="standing_instruction")
    out = json.loads(t["knowledge_promote"]())
    assert out["refused_shared_kind"] == 1 and out["promoted"] == 0
    assert _shared_titles("org-platform") == []


def test_tool_dry_run_writes_nothing(tools):
    t, _ = tools
    t["knowledge_record"](title="d", body="b", kind="decision")
    out = json.loads(t["knowledge_promote"](dry_run=True))
    assert out["copied"] == 1 and out["dry_run"] is True
    assert _shared_titles("org-platform") == []


def test_tool_explicit_namespace_and_entry_id(tools):
    t, _ = tools
    a = json.loads(t["knowledge_record"](title="a", body="x", kind="decision"))["id"]
    json.loads(t["knowledge_record"](title="b", body="y", kind="decision"))
    out = json.loads(t["knowledge_promote"](entry_id=str(a), to="akonteo"))
    assert out["promoted"] == 1 and out["target_namespace"] == "akonteo"
    assert _shared_titles("akonteo") == ["a"]
