# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: IBM Bob lifecycle hooks. Bob compacts a task around 190k
#              tokens and caps it at 270k, and only SessionStart and
#              UserPromptSubmit can put text in front of the model, so a
#              session is secured two ways: a journal of what the agent
#              changed, written by the hooks themselves into an automatic
#              digest on Stop / PreCompact, and nudges (or an opt-in gate)
#              that get the model to record its own checkpoint in time.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

# Bob's file-writing tools and the input key holding the target path.
_WRITE_TOOLS = frozenset(
    {
        "write_to_file",
        "write_file",
        "apply_diff",
        "search_and_replace",
        "insert_content",
    }
)
_COMMAND_TOOLS = frozenset({"execute_command"})
# cgh MCP tools that count as the model saving its own work.
_SAVE_TOOLS = frozenset({"checkpoint", "compact_session", "knowledge_record"})

_NUDGE_EVERY = 40  # tool calls since the last save before a prompt is nudged
_JOURNAL_COMMANDS = 15  # commands kept in a digest


def _payload() -> dict:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _field(payload: dict, *names: str):
    """Bob's docs name the fields event/tool/input; the runtime sends
    hook_event_name/tool_name/tool_input. Accept either."""
    for name in names:
        if payload.get(name) not in (None, ""):
            return payload[name]
    return None


def _root(payload: dict) -> Path | None:
    from codegraph.core.config import find_codegraph_root

    return find_codegraph_root(payload.get("cwd") or os.getcwd())


def _session_id(payload: dict) -> str:
    return str(payload.get("session_id") or "unknown")


def _state_dir(root: Path) -> Path:
    path = root / ".codegraph" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "unknown"


def _state_path(root: Path, session_id: str) -> Path:
    return _state_dir(root) / f"bob-{_safe(session_id)}.json"


def _journal_path(root: Path, session_id: str) -> Path:
    return _state_dir(root) / f"bob-{_safe(session_id)}.jsonl"


def _load_state(root: Path, session_id: str) -> dict:
    try:
        return json.loads(_state_path(root, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"calls": 0, "since_save": 0, "gated_at": 0}


def _save_state(root: Path, session_id: str, state: dict) -> None:
    _state_path(root, session_id).write_text(json.dumps(state), encoding="utf-8")


def _settings(root: Path) -> dict:
    """The [bob] table: checkpoint_every (nudge threshold) and
    checkpoint_gate (0 = off, else block a tool past that many calls)."""
    import tomllib

    try:
        data = tomllib.loads(
            (root / ".codegraph" / "config.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    bob = data.get("bob", {}) if isinstance(data.get("bob"), dict) else {}
    try:
        every = int(bob.get("checkpoint_every", _NUDGE_EVERY))
    except (TypeError, ValueError):
        every = _NUDGE_EVERY
    try:
        gate = int(bob.get("checkpoint_gate", 0))
    except (TypeError, ValueError):
        gate = 0
    return {"every": max(every, 1), "gate": max(gate, 0)}


def _mcp_tool_name(tool: str, tool_input: dict) -> str | None:
    """The cgh tool behind a Bob MCP call, or None for any other tool."""
    if "mcp" not in tool.lower():
        return None
    name = tool_input.get("tool_name") or tool_input.get("name")
    return str(name) if name else None


def _is_save(tool: str, tool_input: dict) -> bool:
    return (_mcp_tool_name(tool, tool_input) or "") in _SAVE_TOOLS


def cmd_bob_session_start(args: argparse.Namespace) -> None:
    """SessionStart: name the session so the model can tag its saves,
    then the usual resume header."""
    try:
        payload = _payload()
        root = _root(payload)
        if root is None:
            return
        session_id = _session_id(payload)
        _save_state(root, session_id, {"calls": 0, "since_save": 0, "gated_at": 0})
        print(
            f"cgh session id for this Bob task: {session_id}. Pass it as "
            "session_id to the codegraph checkpoint and compact_session tools. "
            "Bob compacts long tasks without warning, so checkpoint after each "
            "finished step."
        )
        from codegraph.cli.commands_session import print_resume_header

        print_resume_header(root, payload)
    except Exception:
        pass  # a lifecycle hook must never break the session


def cmd_bob_tool_log(args: argparse.Namespace) -> None:
    """PostToolUse: journal what the agent changed and count calls since
    the model last saved."""
    try:
        payload = _payload()
        root = _root(payload)
        if root is None:
            return
        session_id = _session_id(payload)
        tool = str(_field(payload, "tool_name", "tool") or "")
        tool_input = _field(payload, "tool_input", "input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        entry: dict = {"ts": time.time(), "tool": tool}
        if tool in _WRITE_TOOLS and tool_input.get("path"):
            entry["path"] = str(tool_input["path"])
        elif tool in _COMMAND_TOOLS and tool_input.get("command"):
            entry["command"] = str(tool_input["command"])[:200]
        mcp = _mcp_tool_name(tool, tool_input)
        if mcp:
            entry["mcp"] = mcp
        with _journal_path(root, session_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        state = _load_state(root, session_id)
        state["calls"] = state.get("calls", 0) + 1
        if _is_save(tool, tool_input):
            state["since_save"] = 0
            state["gated_at"] = 0
        else:
            state["since_save"] = state.get("since_save", 0) + 1
        _save_state(root, session_id, state)
    except Exception:
        pass


def cmd_bob_prompt(args: argparse.Namespace) -> None:
    """UserPromptSubmit: one line asking for a checkpoint once the session
    has run checkpoint_every tool calls without saving."""
    try:
        payload = _payload()
        root = _root(payload)
        if root is None:
            return
        session_id = _session_id(payload)
        since = _load_state(root, session_id).get("since_save", 0)
        if since >= _settings(root)["every"]:
            print(
                f"cgh: {since} tool calls since this task last saved to cgh. "
                "Before going on, call the codegraph checkpoint tool "
                f'(session_id="{session_id}") with what is done, the decisions '
                "made and what is still open: Bob may compact this task at any "
                "point and drop the early details."
            )
    except Exception:
        pass


def cmd_bob_gate(args: argparse.Namespace) -> None:
    """PreToolUse, opt-in ([bob] checkpoint_gate > 0): past that many calls
    without a save, block one tool call with the reason, so the model
    checkpoints first. Saves themselves always pass; one block per crossing."""
    try:
        payload = _payload()
        root = _root(payload)
        if root is None:
            return
        gate = _settings(root)["gate"]
        if not gate:
            return
        tool = str(_field(payload, "tool_name", "tool") or "")
        tool_input = _field(payload, "tool_input", "input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        if _is_save(tool, tool_input):
            return
        session_id = _session_id(payload)
        state = _load_state(root, session_id)
        since = state.get("since_save", 0)
        if since < gate or state.get("gated_at", 0):
            return
        state["gated_at"] = since
        _save_state(root, session_id, state)
        reason = (
            f"cgh: {since} tool calls without saving. Call the codegraph "
            f'checkpoint tool (session_id="{session_id}") with the work done, '
            "decisions and open threads, then retry this call."
        )
        print(reason)
        print(reason, file=sys.stderr)
        sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        pass


def _digest(root: Path, session_id: str) -> tuple[str, int] | None:
    try:
        lines = _journal_path(root, session_id).read_text(encoding="utf-8")
    except OSError:
        return None
    entries = []
    for line in lines.splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    if not entries:
        return None
    files: list[str] = []
    for e in entries:
        path = e.get("path")
        if path and path not in files:
            files.append(path)
    commands = [e["command"] for e in entries if e.get("command")]
    cgh_calls = Counter(e["mcp"] for e in entries if e.get("mcp"))
    parts = [f"{len(entries)} tool calls."]
    if files:
        parts.append(f"Files written ({len(files)}): " + ", ".join(files[:40]))
    if commands:
        parts.append("Last commands: " + " | ".join(commands[-_JOURNAL_COMMANDS:]))
    if cgh_calls:
        parts.append(
            "cgh tools: "
            + ", ".join(f"{name} x{n}" for name, n in cgh_calls.most_common(10))
        )
    return "\n".join(parts), len(files)


def cmd_bob_stop(args: argparse.Namespace) -> None:
    """Stop / PreCompact: turn the journal into an automatic session digest,
    one per session, superseded each time, so a compaction or a closed task
    still leaves what was changed in the next session's resume bundle."""
    try:
        payload = _payload()
        root = _root(payload)
        if root is None:
            return
        session_id = _session_id(payload)
        made = _digest(root, session_id)
        if made is None:
            return
        body, _ = made
        event = str(_field(payload, "hook_event_name", "event") or "Stop")
        from codegraph.state.call_log import knowledge_list, knowledge_record

        previous = knowledge_list(
            tag="auto-digest", session_id=session_id, limit=1, repo_root=root
        )
        knowledge_record(
            title=f"Bob session {session_id} (automatic, {event})",
            body=(
                body + "\nRecorded by the cgh Bob hooks, not the model: "
                "what changed, not why. Look for a model checkpoint of the same "
                "session for the decisions."
            ),
            kind="note",
            tags="session-digest,auto-digest,bob",
            session_id=session_id,
            repo_root=root,
            supersedes=previous[0]["id"] if previous else 0,
        )
    except Exception:
        pass
