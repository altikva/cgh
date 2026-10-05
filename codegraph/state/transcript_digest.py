# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A mechanical session digest read from a Claude Code transcript
#              (the JSONL file a SessionEnd / PreCompact hook is handed):
#              the user's last requests, the files edited, the commands run
#              and the end of the last answer. Written without the model, so
#              a /clear or a compaction never loses what the session did.

from __future__ import annotations

import json
from pathlib import Path

# Only the end of a long transcript matters, and one can run to hundreds of MB.
_TAIL_BYTES = 4 * 1024 * 1024
_EDIT_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
_REQUESTS = 5
_COMMANDS = 10
_CLIP = 240


def _tail_lines(path: Path) -> list[str]:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        start = max(0, size - _TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if start else lines  # the first line is cut mid-way


def _clip(text: str, limit: int = _CLIP) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _user_text(content) -> str:
    """The typed request, or "" for tool results, hook output, slash-command
    echoes and other harness-injected messages."""
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        content = " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    text = str(content or "").strip()
    return "" if not text or text.startswith("<") else text


def digest_from_transcript(path: str | Path) -> dict | None:
    """Requests, edited files, commands and the last answer of a session,
    or None when the transcript is missing or holds nothing worth keeping."""
    path = Path(path)
    try:
        lines = _tail_lines(path)
    except OSError:
        return None
    requests: list[str] = []
    files: list[str] = []
    commands: list[str] = []
    last_answer = ""
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isMeta"):
            continue
        message = entry.get("message") or {}
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if entry.get("type") == "user":
            text = _user_text(content)
            if text:
                requests.append(_clip(text))
        elif entry.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    last_answer = block["text"]
                elif block.get("type") == "tool_use":
                    tool_input = block.get("input") or {}
                    key = _EDIT_TOOLS.get(block.get("name", ""))
                    if key and tool_input.get(key):
                        target = str(tool_input[key])
                        if target in files:
                            files.remove(target)
                        files.append(target)  # most recent last
                    elif block.get("name") == "Bash" and tool_input.get("command"):
                        commands.append(_clip(str(tool_input["command"]), 160))
    if not (requests or files or commands or last_answer):
        return None
    return {
        "requests": requests[-_REQUESTS:],
        "files": files,
        "commands": commands[-_COMMANDS:],
        "last_answer": _clip(last_answer, 600),
    }


def format_digest(digest: dict, root: Path | None = None) -> str:
    def short(p: str) -> str:
        if root is not None:
            try:
                return str(Path(p).resolve().relative_to(root.resolve()))
            except (ValueError, OSError):
                pass
        return p

    parts: list[str] = []
    if digest["requests"]:
        parts.append(
            "Last requests:\n" + "\n".join(f"- {r}" for r in digest["requests"])
        )
    if digest["files"]:
        shown = [short(f) for f in digest["files"][-30:]]
        parts.append(f"Files edited ({len(digest['files'])}): " + ", ".join(shown))
    if digest["commands"]:
        parts.append("Last commands: " + " | ".join(digest["commands"]))
    if digest["last_answer"]:
        parts.append("Last answer (end): " + digest["last_answer"])
    return "\n".join(parts)
