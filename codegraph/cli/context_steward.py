# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The context steward (CGH_CONTEXT_STEWARD=1): keeps what enters
#              an agent's context in check.
#              gauge_message   after a tool call, reads the session's context
#                              size from its transcript; past the checkpoint
#                              threshold it asks for a checkpoint, past the
#                              relay threshold for a checkpoint and a fresh
#                              session, and drops a relay request a
#                              supervisor can act on (an agent cannot run
#                              /clear on itself)
#              agent_brief     before a sub-agent starts, adds to its brief
#                              the code locations cgh finds for it, so it
#                              does not explore the tree again
#              Both are silent when the steward is off or nothing applies.

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Context sizes are in tokens: what the last model call read (input, cache
# read and cache write together), which every following turn pays again.
_DEFAULT_CHECKPOINT = 100_000
_DEFAULT_RELAY = 150_000
# Past the relay threshold, say it again only after this much more growth.
_RELAY_REPEAT = 25_000
# The transcript tail holding the last assistant turns; reading the whole
# file on every tool call would cost more as the session grows.
_TAIL_BYTES = 256 * 1024
_MAX_LOCATIONS = 12
RELAY_READY = "CGH-RELAY-READY"


def enabled() -> bool:
    return os.environ.get("CGH_CONTEXT_STEWARD") == "1"


def _threshold(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def thresholds() -> tuple[int, int]:
    return (
        _threshold("CGH_CONTEXT_CHECKPOINT", _DEFAULT_CHECKPOINT),
        _threshold("CGH_CONTEXT_RELAY", _DEFAULT_RELAY),
    )


def context_size(transcript: Path) -> int | None:
    """Tokens the session's last model call read, from the transcript tail.
    None when the transcript holds no usage yet."""
    try:
        with transcript.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if '"usage"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue  # the first line of the tail may be cut
        if entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            return sum(
                int(usage.get(k) or 0)
                for k in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
    return None


def _state_dir(root: Path) -> Path:
    return root / ".codegraph" / "steward"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gauge_message(root: Path, session_id: str, transcript: Path) -> str | None:
    """What to tell the agent after a tool call, or None. Each threshold is
    announced once per session; the relay one again every _RELAY_REPEAT."""
    size = context_size(transcript)
    if size is None or not session_id:
        return None
    checkpoint_at, relay_at = thresholds()
    state_path = _state_dir(root) / f"{session_id}.json"
    state = _load_state(state_path)
    message = None
    if size >= relay_at:
        last = int(state.get("relay_told_at") or 0)
        if not last or size >= last + _RELAY_REPEAT:
            state["relay_told_at"] = size
            _write_relay_request(root, session_id, size)
            message = (
                f"[cgh] Context is at {size // 1000}k tokens, past the relay "
                f"threshold ({relay_at // 1000}k). Every turn now re-reads all of "
                "it. Do this now:\n"
                f"  1. call checkpoint(session_id={session_id!r}, digest=...) with "
                "the goal, what is done, the decisions, what is open and the "
                "next step;\n"
                "  2. finish only the step in hand, then stop and end your reply "
                f"with {RELAY_READY}.\n"
                "A supervisor starts a fresh session that resumes from the "
                "checkpoint. Without one, ask the user to run /clear: the next "
                "session gets the resume header."
            )
    elif size >= checkpoint_at and not state.get("checkpoint_told_at"):
        state["checkpoint_told_at"] = size
        message = (
            f"[cgh] Context is at {size // 1000}k tokens (checkpoint threshold "
            f"{checkpoint_at // 1000}k). Call checkpoint(session_id="
            f"{session_id!r}, digest=...) now with the goal, what is done, the "
            "decisions and the next step, so a relay or a /clear loses nothing. "
            "Then go on."
        )
    if message:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass  # best effort: at worst the message comes again
    return message


def _write_relay_request(root: Path, session_id: str, size: int) -> None:
    """The file a supervisor watches: which session asks to be relayed."""
    path = _state_dir(root) / "relay-request.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"session_id": session_id, "context_tokens": size, "at": time.time()}
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # the message to the agent still goes out


def agent_brief(root: Path, tool_input: dict) -> dict | None:
    """The sub-agent's tool input with cgh's locations appended to its
    prompt, or None when there is nothing to add."""
    prompt = tool_input.get("prompt")
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or "[cgh] Start from" in prompt
    ):
        return None
    from codegraph.analysis.locations import task_locations

    try:
        locs = task_locations(root, prompt, max_lines=_MAX_LOCATIONS)
    except Exception:
        return None
    if not locs:
        return None
    block = (
        "\n\n[cgh] Start from these locations, found in the code graph for this "
        "brief (Read with offset/limit around each; use cgh for the rest instead "
        "of exploring the tree):\n" + "\n".join(f"  {loc}" for loc in locs)
    )
    return {**tool_input, "prompt": prompt + block}
