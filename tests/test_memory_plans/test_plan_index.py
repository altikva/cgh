"""Tests for plan_index.py — scanning Claude Code plan files."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_plans_env(tmp_path: Path, monkeypatch) -> Path:
    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setenv("CG_PLANS_DIR", str(plans))
    (tmp_path / ".codegraph").mkdir(exist_ok=True)
    return plans


class TestParseFilename:
    def test_slug_only(self):
        from codegraph.plan_index import parse_filename

        assert parse_filename("nifty-popping-sprout") == ("nifty-popping-sprout", "")

    def test_with_agent(self):
        from codegraph.plan_index import parse_filename

        slug, agent = parse_filename("crispy-dancing-wombat-agent-a621be379")
        assert slug == "crispy-dancing-wombat"
        assert agent == "a621be379"

    def test_multi_dashed_slug(self):
        from codegraph.plan_index import parse_filename

        slug, agent = parse_filename("foo-bar-baz-qux-agent-deadbeef")
        assert slug == "foo-bar-baz-qux"
        assert agent == "deadbeef"


class TestScanPlanDir:
    def test_indexes_plans(self, tmp_plans_env, tmp_path):
        (tmp_plans_env / "alpha.md").write_text("# Alpha Plan\n\nSome description", encoding="utf-8")
        (tmp_plans_env / "beta-agent-a1b2c3d4.md").write_text(
            "# Beta Sub-Agent Plan\n\nSub-task details", encoding="utf-8"
        )

        from codegraph.plan_index import scan_plan_dir

        stats = scan_plan_dir(tmp_path)
        assert stats["indexed"] == 2

    def test_agent_id_persisted(self, tmp_plans_env, tmp_path):
        (tmp_plans_env / "gamma-agent-cafebabe.md").write_text("# Gamma Plan\n\nAgent-driven", encoding="utf-8")
        from codegraph.fts import get_fts_conn, list_plan_entries
        from codegraph.plan_index import scan_plan_dir

        scan_plan_dir(tmp_path)
        conn = get_fts_conn(tmp_path)
        entries = list_plan_entries(conn)
        assert any(e.agent_id == "cafebabe" for e in entries)

    def test_search_matches_title(self, tmp_plans_env, tmp_path):
        (tmp_plans_env / "my-feature.md").write_text(
            "# Add donor statistics dashboard\n\nImplementation plan for donor cohorts",
            encoding="utf-8",
        )
        from codegraph.fts import get_fts_conn, plan_search
        from codegraph.plan_index import scan_plan_dir

        scan_plan_dir(tmp_path)
        conn = get_fts_conn(tmp_path)
        hits = plan_search(conn, "donor statistics")
        assert hits
        assert hits[0].slug == "my-feature"

    def test_agent_only_filter(self, tmp_plans_env, tmp_path):
        (tmp_plans_env / "plain.md").write_text("# Plain\n\nbody", encoding="utf-8")
        (tmp_plans_env / "sub-agent-deadbeef.md").write_text("# Sub\n\nbody", encoding="utf-8")

        from codegraph.fts import get_fts_conn, list_plan_entries
        from codegraph.plan_index import scan_plan_dir

        scan_plan_dir(tmp_path)
        conn = get_fts_conn(tmp_path)

        all_entries = list_plan_entries(conn)
        agent_only = list_plan_entries(conn, agent_only=True)
        assert len(all_entries) == 2
        assert len(agent_only) == 1
        assert agent_only[0].agent_id == "deadbeef"
