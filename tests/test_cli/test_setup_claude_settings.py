# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-26
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh setup claude` against settings a person already wrote:
#              their non-ASCII text survives the rewrite, a hand-written
#              reindex-on-commit hook is not doubled, a cgh marker in any
#              command of an entry is recognised, and a second run changes
#              nothing.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codegraph.cli import commands_init as ci

USER_TEXT = "⏱️  Deploy triggered — run /deploy-status"
HAND_REINDEX = "cgh diff --since HEAD~1; cgh index"


def _setup(root: Path) -> None:
    ci.cmd_setup(argparse.Namespace(root=str(root), target="claude", force=False))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")


def _commit_commands(settings: dict) -> list[str]:
    return [
        hook["command"]
        for group in settings.get("hooks", {}).get("PostToolUse", [])
        if group.get("matcher") == "Bash(git commit*)"
        for hook in group["hooks"]
    ]


def _user_settings() -> dict:
    return {
        "zeta": 1,
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash(gh workflow run*)",
                    "hooks": [{"type": "command", "command": f"echo '{USER_TEXT}'"}],
                }
            ]
        },
        "alpha": "café",
    }


def test_non_ascii_text_round_trips(tmp_path):
    shared = tmp_path / ".claude" / "settings.json"
    _write(shared, _user_settings())

    _setup(tmp_path)

    raw = shared.read_text(encoding="utf-8")
    assert USER_TEXT in raw
    assert "café" in raw
    assert "\\u" not in raw
    data = json.loads(raw)
    assert list(data)[:3] == ["zeta", "hooks", "alpha"]
    assert (
        data["hooks"]["PostToolUse"][0] == _user_settings()["hooks"]["PostToolUse"][0]
    )
    # Every other file cgh writes for Claude keeps UTF-8 as is too.
    for name in (".claude/settings.local.json", ".mcp.json"):
        assert "\\u" not in (tmp_path / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("where", ["settings.json", "settings.local.json"])
def test_hand_written_reindex_hook_is_not_doubled(tmp_path, where):
    path = tmp_path / ".claude" / where
    _write(
        path,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash(git commit*)",
                        "hooks": [{"type": "command", "command": HAND_REINDEX}],
                    }
                ]
            }
        },
    )

    _setup(tmp_path)

    commands = []
    for name in ("settings.json", "settings.local.json"):
        f = tmp_path / ".claude" / name
        if f.exists():
            commands += _commit_commands(json.loads(f.read_text(encoding="utf-8")))
    assert commands == [HAND_REINDEX]
    assert ci.audit_claude_integration(tmp_path)["hooks"]["missing"] == []


def test_older_duplicate_next_to_a_hand_written_hook_is_dropped():
    spec = next(
        s
        for s in ci._claude_hook_specs("cgh")
        if s["marker"] == "cgh-reindex-on-commit"
    )
    shared = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash(git commit*)",
                    "hooks": [{"type": "command", "command": HAND_REINDEX}],
                }
            ]
        }
    }
    ci._append_hook(shared, spec)

    result = ci._ensure_claude_hooks(shared, {}, "cgh")

    assert _commit_commands(shared) == [HAND_REINDEX]
    assert result["shared_changed"]


@pytest.mark.parametrize(
    "command",
    [
        "/Users/x/.local/bin/cgh index --root .",
        "python -P -m codegraph index",
        '"C:/Program Files/cgh/cghw.exe" index',
        "codegraph index && echo ok",
    ],
)
def test_reindex_command_forms_are_recognised(command):
    assert ci._REINDEX_COMMAND_RE.search(command)


@pytest.mark.parametrize(
    "command", ["cgh indexer", "echo cgh-index", "cgh status", "mycgh index"]
)
def test_other_commands_are_not_taken_for_a_reindex(command):
    assert not ci._REINDEX_COMMAND_RE.search(command)


def test_marker_in_a_later_command_is_recognised():
    spec = next(
        s for s in ci._claude_hook_specs("cgh") if s["marker"] == "cgh-precheck-bash"
    )
    local = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "echo mine"},
                        {"type": "command", "command": spec["command"]},
                    ],
                }
            ]
        }
    }

    assert ci._find_hook(local, spec)
    assert ci._hook_command(local, spec) == spec["command"]
    result = ci._ensure_claude_hooks({}, local, "cgh")
    assert spec["label"] not in result["added"]
    tagged = [
        command
        for group in local["hooks"]["PreToolUse"]
        for command in ci._entry_commands(group)
        if spec["marker"] in command
    ]
    assert tagged == [spec["command"]]

    # Dropping it keeps the user's own command in the same entry.
    assert ci._drop_hook(local, spec)
    bash = [g for g in local["hooks"]["PreToolUse"] if g["matcher"] == "Bash"]
    assert [h["command"] for g in bash for h in g["hooks"]] == ["echo mine"]


def test_setup_twice_is_idempotent(tmp_path):
    _write(tmp_path / ".claude" / "settings.json", _user_settings())

    _setup(tmp_path)
    first = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    _setup(tmp_path)
    second = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}

    assert first == second
