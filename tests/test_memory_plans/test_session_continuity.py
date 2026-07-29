# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Session continuity tests: the standing_instruction kind,
#              supersede links hiding replaced entries, the resume bundle
#              (priority order, budget cap, instructions never dropped),
#              federated read-only knowledge search, the lifecycle hook
#              handlers, and the hook specs shipping the new events.

from __future__ import annotations

import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

import codegraph.state.call_log as call_log
from codegraph.server.tools_session import build_resume_bundle
from codegraph.state.call_log import (
    knowledge_list,
    knowledge_record,
    knowledge_search,
    knowledge_search_ro,
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fresh call_log connection bound to this tmp repo."""
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr(call_log, "_conn", None)
    yield tmp_path
    conn = call_log._conn
    if conn is not None:
        conn.close()
    call_log._conn = None


class TestStandingInstructionsAndSupersede:
    def test_standing_instruction_kind_is_valid(self, repo):
        knowledge_record(
            "Always use the commit skill",
            "Never raw git commit.",
            kind="standing_instruction",
            repo_root=repo,
        )
        rows = knowledge_list(kind="standing_instruction", repo_root=repo)
        assert len(rows) == 1
        assert rows[0]["kind"] == "standing_instruction"

    def test_supersede_hides_the_old_entry(self, repo):
        old = knowledge_record("Rule v1", "use X", kind="decision", repo_root=repo)
        new = knowledge_record(
            "Rule v2", "use Y instead", kind="decision", repo_root=repo, supersedes=old
        )
        ids = [r["id"] for r in knowledge_list(repo_root=repo)]
        assert new in ids and old not in ids
        hits = [h["id"] for h in knowledge_search("Rule", repo_root=repo)]
        assert new in hits and old not in hits


class TestResumeBundle:
    def test_priority_and_content(self, repo):
        knowledge_record(
            "No proposal refs",
            "Never cite internal proposals in shipped text.",
            kind="standing_instruction",
            repo_root=repo,
        )
        knowledge_record(
            "Session digest s1",
            "We shipped the guard.",
            tags="session-digest",
            session_id="s1",
            repo_root=repo,
        )
        knowledge_record(
            "DuckDB lock gotcha", "RO opens blocked while owner alive.",
            kind="gotcha", repo_root=repo,
        )  # fmt: skip

        bundle = build_resume_bundle(repo, session_id="s1", task="DuckDB lock")
        assert [r["title"] for r in bundle["standing_instructions"]] == [
            "No proposal refs"
        ]
        assert any("s1" in d["title"] for d in bundle["digests"])
        assert any("DuckDB" in k["title"] for k in bundle["knowledge"])
        assert bundle["truncated"] is False

    def test_budget_caps_but_never_drops_instructions(self, repo):
        knowledge_record(
            "Instruction", "x" * 3000, kind="standing_instruction", repo_root=repo
        )
        for i in range(10):
            knowledge_record(
                f"Digest {i}", "y" * 3000, tags="session-digest", repo_root=repo
            )

        bundle = build_resume_bundle(repo, budget_kb=4)
        assert len(bundle["standing_instructions"]) == 1  # never dropped
        assert bundle["truncated"] is True
        total = sum(len(json.dumps(v)) for v in bundle.values() if isinstance(v, list))
        assert total < 16 * 1024  # far below the uncapped size

    def test_checkpoint_supersedes_previous_digest(self, repo, monkeypatch):
        import codegraph.server as srv

        monkeypatch.setattr(srv, "_root", repo)
        first = knowledge_record(
            "Session digest s9", "v1", tags="compaction,session-digest",
            session_id="s9", repo_root=repo,
        )  # fmt: skip
        second = knowledge_record(
            "Session digest s9", "v2", tags="compaction,session-digest",
            session_id="s9", repo_root=repo, supersedes=first,
        )  # fmt: skip
        digests = knowledge_list(tag="session-digest", repo_root=repo)
        assert [d["id"] for d in digests] == [second]


class TestFederatedKnowledge:
    def test_ro_search_reads_a_child_store(self, repo, monkeypatch):
        child = repo / "child"
        (child / ".codegraph").mkdir(parents=True)
        # Write into the child store through a temporarily rebound conn.
        monkeypatch.setattr(call_log, "_conn", None)
        knowledge_record(
            "API contract lives here", "types come from the api repo",
            kind="decision", repo_root=child,
        )  # fmt: skip
        call_log._conn.close()
        monkeypatch.setattr(call_log, "_conn", None)

        hits = knowledge_search_ro(
            child / ".codegraph" / "call_log.db", "contract", limit=5
        )
        assert len(hits) == 1
        assert hits[0]["title"] == "API contract lives here"
        assert knowledge_search_ro(child / "nope.db", "contract") == []


class TestLifecycleHooks:
    def _payload(self, root: Path, extra: dict | None = None) -> io.StringIO:
        return io.StringIO(json.dumps({"cwd": str(root), **(extra or {})}))

    def test_hook_checkpoint_records_and_supersedes(self, repo, monkeypatch):
        from codegraph.cli.commands_session import cmd_hook_checkpoint

        for trigger in ("PreCompact", "PreCompact"):
            monkeypatch.setattr(
                "sys.stdin",
                self._payload(repo, {"session_id": "s1", "trigger": trigger}),
            )
            cmd_hook_checkpoint(Namespace())

        rows = knowledge_list(tag="auto-checkpoint", repo_root=repo)
        assert len(rows) == 1  # the second superseded the first

    def test_hook_resume_header_prints_when_store_has_content(
        self, repo, monkeypatch, capsys
    ):
        from codegraph.cli.commands_session import cmd_hook_resume_header

        monkeypatch.setattr("sys.stdin", self._payload(repo))
        cmd_hook_resume_header(Namespace())
        assert capsys.readouterr().out == ""  # empty store stays silent

        knowledge_record("Rule", "x", kind="standing_instruction", repo_root=repo)
        monkeypatch.setattr("sys.stdin", self._payload(repo))
        cmd_hook_resume_header(Namespace())
        out = capsys.readouterr().out
        assert "resume bundle" in out and "1 standing instruction" in out


class TestHookSpecs:
    def test_lifecycle_specs_ship_and_render_without_matcher(self):
        from codegraph.cli.commands_init import (
            _append_hook,
            _claude_hook_specs,
            _find_hook,
        )

        specs = {s["marker"]: s for s in _claude_hook_specs("cgh")}
        for marker in (
            "cgh-auto-checkpoint",
            "cgh-auto-checkpoint-end",
            "cgh-resume-header",
        ):
            assert marker in specs

        settings: dict = {}
        spec = specs["cgh-resume-header"]
        _append_hook(settings, spec)
        entry = settings["hooks"]["SessionStart"][0]
        assert "matcher" not in entry  # lifecycle events carry no matcher
        assert _find_hook(settings, spec)
