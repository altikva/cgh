# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the check_action MCP tool over a real knowledge
#              store: the answer shape, no_rule with related, an empty store
#              that never answers allowed, a superseded rule that no longer
#              rules, shared stores that are never a source of rules, and the
#              rules knowledge_record echoes back.

from __future__ import annotations

import json
import time

import pytest

import cgh.server as _srv
import cgh.state.call_log as cl
from cgh.state.call_log import knowledge_record

_KEYS = {
    "action",
    "verdict",
    "recognised",
    "rule",
    "actions",
    "others",
    "ignored",
    "next",
}

_BODY = (
    "Never push to an integration branch: every change goes through a pull "
    "request. Releases are discussed before the tag.\n\n"
    "cgh-rule: refuse git.push branch=main|master|develop\n"
    "cgh-rule: ask git.tag tag=v*\n"
)


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *_args, **_kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr("cgh.core.config.GLOBAL_DIR", tmp_path / "home" / ".cgh")
    root = (tmp_path / "repo").resolve()
    (root / ".cgh").mkdir(parents=True)
    cl.reset_for_tests()
    _srv._root = root
    monkeypatch.setattr(_srv, "_call_origin", lambda: "agent")
    # The branch of the checkout is the test's to choose, not the machine's.
    monkeypatch.setattr(
        "cgh.state.action_rules.current_branch", lambda _p, timeout=0: "feature/x"
    )

    from cgh.server import _register_decision, _register_knowledge

    m = _FakeMcp()
    _register_decision(m)
    _register_knowledge(m)
    m.tools["root"] = root
    yield m.tools
    cl.reset_for_tests()
    _srv._root = None


def _check(tools, action: str, session_id: str = "") -> dict:
    return json.loads(tools["check_action"](action=action, session_id=session_id))


def _rule(root, body: str = _BODY, title: str = "No direct push", **kwargs) -> int:
    return knowledge_record(
        title, body, kind="standing_instruction", repo_root=root, **kwargs
    )


def test_matched_answer_shape(tools):
    entry_id = _rule(tools["root"])
    out = _check(tools, "git push origin develop")
    assert set(out) == _KEYS
    assert out["verdict"] == "refused"
    assert out["recognised"] is True
    assert set(out["rule"]) == {"entry_id", "title", "line", "reason", "recorded"}
    assert out["rule"]["entry_id"] == entry_id
    assert out["rule"]["title"] == "No direct push"
    assert out["rule"]["line"] == "cgh-rule: refuse git.push branch=main|master|develop"
    assert "cgh-rule" not in out["rule"]["reason"]
    assert out["rule"]["recorded"] == time.strftime(
        "%Y-%m-%dT%H:%MZ", time.gmtime(cl.knowledge_get(entry_id, tools["root"])["ts"])
    )
    assert out["actions"] == [
        {
            "verb": "git.push",
            "fields": {"remote": "origin", "branch": "develop"},
            "raw": "git push origin develop",
            "verdict": "refused",
            "rule_entry_id": entry_id,
        }
    ]
    assert out["others"] == [] and out["ignored"] == []


def test_tag_without_a_go_and_feature_push(tools):
    _rule(tools["root"])
    assert _check(tools, "git tag v0.22.0")["verdict"] == "ask"
    # The current branch is feature/x: a bare push is not refused, and with no
    # allow rule it is not allowed either.
    assert _check(tools, "git push")["verdict"] == "no_rule"
    _rule(tools["root"], "cgh-rule: allow git.push branch=feature/*", "Topic pushes")
    assert _check(tools, "git push")["verdict"] == "allowed"
    assert _check(tools, "git push origin develop")["verdict"] == "refused"


def test_an_empty_store_never_answers_allowed(tools):
    for action in (
        "git push origin develop",
        "git tag v0.22.0",
        "rm -rf /",
        "merge PR 3",
    ):
        out = _check(tools, action)
        assert out["verdict"] == "no_rule", action
        assert set(out) == _KEYS | {"related"}
        assert out["rule"] is None
        assert out["related"] == []


def test_instructions_without_an_allow_line_never_answer_allowed(tools):
    root = tools["root"]
    _rule(root)
    _rule(root, "Pushing a topic branch is always fine, go ahead.", "Topic pushes ok")
    out = _check(tools, "git push origin feature/x")
    assert out["verdict"] == "no_rule"
    assert out["rule"] is None


def test_no_rule_lists_related_instructions(tools):
    root = tools["root"]
    prose = _rule(root, "A release is discussed before the tag is cut.", "Release tag")
    out = _check(tools, "git tag v0.22.0")
    assert out["verdict"] == "no_rule"
    assert out["recognised"] is True
    assert [h["id"] for h in out["related"]] == [prose]
    assert set(out["related"][0]) == {"id", "title", "confidence"}
    assert len(out["related"]) <= 3


def test_related_only_holds_standing_instructions(tools):
    root = tools["root"]
    knowledge_record(
        "Tag naming", "a tag is named v0.22.0", kind="note", repo_root=root
    )
    assert _check(tools, "git tag v0.22.0")["related"] == []


def test_unrecognised_input(tools):
    _rule(tools["root"], "cgh-rule: allow *", "Everything goes")
    for text in ("", "faire le necessaire", "git push 'open"):
        out = _check(tools, text)
        assert out["verdict"] == "no_rule", text
        assert out["recognised"] is False
        assert out["actions"] == [] and out["related"] == []
        assert out["action"] == text


def test_a_superseded_rule_no_longer_rules(tools):
    root = tools["root"]
    old = _rule(root)
    assert _check(tools, "git push origin develop")["verdict"] == "refused"
    _rule(
        root, "Withdrawn: pushes are no longer ruled here.", "Withdrawn", supersedes=old
    )
    out = _check(tools, "git push origin develop")
    assert out["verdict"] == "no_rule"
    assert out["rule"] is None and out["others"] == []


def test_only_standing_instructions_rule(tools):
    knowledge_record(
        "A note quoting a rule", _BODY, kind="note", repo_root=tools["root"]
    )
    knowledge_record(
        "A decision quoting a rule", _BODY, kind="decision", repo_root=tools["root"]
    )
    assert _check(tools, "git push origin develop")["verdict"] == "no_rule"


def _plant_shared_rule(namespace: str, body: str) -> None:
    """Write a standing instruction straight into a shared store, as a
    tampered or foreign store would hold one: the API refuses to."""
    conn = cl._get_conn_at(cl.shared_store_path(namespace))
    conn.execute(
        "INSERT INTO knowledge(session_id, title, body, tags, kind, file_refs, ts, "
        "namespace) VALUES ('', 'Fleet rule', ?, '', 'standing_instruction', '', ?, ?)",
        (body, time.time(), namespace),
    )
    conn.commit()


def test_the_api_refuses_a_rule_in_a_shared_store(tools):
    with pytest.raises(ValueError, match="not allowed in a shared namespace"):
        _rule(tools["root"], namespace="org-platform")


def test_a_shared_store_is_never_a_source_of_rules(tools, monkeypatch):
    root = tools["root"]
    (root / ".cgh" / "config.toml").write_text(
        '[knowledge]\nnamespaces = ["org-platform"]\n', encoding="utf-8"
    )
    _plant_shared_rule("org-platform", "cgh-rule: allow *\ncgh-rule: refuse git.tag")
    # The shared store does hold the row.
    shared = cl.knowledge_search_ro(
        cl.shared_store_path("org-platform"), "cgh-rule", kind="standing_instruction"
    )
    assert len(shared) == 1

    # From here on, opening any shared store is a failure of the test.
    def no_shared(*_a, **_k):
        raise AssertionError("check_action opened a shared store")

    monkeypatch.setattr(cl, "_get_conn_at", no_shared)
    monkeypatch.setattr(cl, "knowledge_search_ro", no_shared)
    monkeypatch.setattr(cl, "shared_store_path", no_shared)

    for action, silent in (
        ("git push origin develop", "no_rule"),
        ("git tag v1", "no_rule"),
    ):
        out = _check(tools, action)
        assert out["verdict"] == silent, action
        assert out["rule"] is None
        assert out["others"] == [] and out["ignored"] == [] and out["related"] == []


def test_a_row_filed_under_a_shared_namespace_in_the_repo_store_does_not_rule(tools):
    root = tools["root"]
    conn = cl._get_conn(root)
    conn.execute(
        "INSERT INTO knowledge(session_id, title, body, tags, kind, file_refs, ts, "
        "namespace) VALUES ('', 'Imported', 'cgh-rule: allow *', '', "
        "'standing_instruction', '', ?, 'org-platform')",
        (time.time(),),
    )
    conn.commit()
    assert cl.standing_rule_entries(root) == []
    assert _check(tools, "git push origin develop")["verdict"] == "no_rule"
    # The same row under the repo namespace does rule: the filter is the cause.
    conn.execute("UPDATE knowledge SET namespace = 'repo'")
    conn.commit()
    assert _check(tools, "git push origin develop")["verdict"] == "allowed"


def test_an_allow_the_calling_session_recorded_is_ignored(tools):
    entry_id = _rule(
        tools["root"], "cgh-rule: allow git.push", "Self-service", session_id="sess-a"
    )
    mine = _check(tools, "git push origin feature/x", session_id="sess-a")
    assert mine["verdict"] == "no_rule"
    assert mine["ignored"] == [
        {"entry_id": entry_id, "line": "cgh-rule: allow git.push"}
    ]
    assert (
        _check(tools, "git push origin feature/x", session_id="sess-b")["verdict"]
        == "allowed"
    )


def test_check_action_records_nothing(tools):
    root = tools["root"]
    _rule(root)
    conn = cl._get_conn(root)

    def counts() -> tuple:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("knowledge", "knowledge_outcome")
        )

    before = counts()
    _check(tools, "git push origin develop")
    _check(tools, "git tag v0.22.0")
    assert counts() == before


def test_check_action_reads_the_root_at_call_time(tools, tmp_path):
    _rule(tools["root"])
    assert _check(tools, "git push origin develop")["verdict"] == "refused"
    other = (tmp_path / "other").resolve()
    (other / ".cgh").mkdir(parents=True)
    _srv._root = other
    assert _check(tools, "git push origin develop")["verdict"] == "no_rule"


def test_check_action_is_a_core_tool():
    from cgh.core.tool_exposure import CORE_TOOLS

    assert "check_action" in CORE_TOOLS


def test_knowledge_record_echoes_rules_and_rule_errors(tools):
    out = json.loads(
        tools["knowledge_record"](
            title="No direct push",
            body=_BODY + "cgh-rule: forbid git.commit\n",
            kind="standing_instruction",
        )
    )
    assert [r["line"] for r in out["rules"]] == [
        "cgh-rule: refuse git.push branch=main|master|develop",
        "cgh-rule: ask git.tag tag=v*",
    ]
    assert out["rules"][0] == {
        "verdict": "refuse",
        "verb": "git.push",
        "constraints": {"branch": ["main", "master", "develop"]},
        "line": "cgh-rule: refuse git.push branch=main|master|develop",
    }
    assert out["rule_errors"] == [
        {
            "line": "cgh-rule: forbid git.commit",
            "error": "unknown verdict 'forbid': use refuse, ask or allow",
        }
    ]
    # The line that did not parse does not rule; the two others do.
    assert _check(tools, "git commit -m x")["verdict"] == "no_rule"
    assert _check(tools, "git push origin main")["verdict"] == "refused"


def test_knowledge_record_stays_quiet_without_rule_lines(tools):
    plain = json.loads(
        tools["knowledge_record"](
            title="Prose", body="No rule line here.", kind="standing_instruction"
        )
    )
    assert "rules" not in plain and "rule_errors" not in plain
    note = json.loads(
        tools["knowledge_record"](title="A note", body=_BODY, kind="note")
    )
    assert "rules" not in note and "rule_errors" not in note
