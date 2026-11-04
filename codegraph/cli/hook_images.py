# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Re-read hint for images in the Read PreToolUse hook. An image
#              read with Read stays in the conversation, so reading the same
#              capture again pays its tokens twice. reread_hint records each
#              image read per session (path, size, cheap fingerprint) under
#              .codegraph/sessions/images-<session>.json and returns an
#              advisory line when the same unchanged file comes back.
#              text_reread_hint does the same for a text file read in full
#              (reads-<session>.json): parallel subagents of one session were
#              seen reading the same file whole, so another agent's Read gets
#              the file's outline to aim at the part it needs. A note beside
#              the Read did not stop the read, so with hook answers on that
#              Read is denied with the plan instead, and repeating it goes
#              through. Any failure returns None.

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
)

# Oldest entries are dropped past this many images per session.
MAX_ENTRIES = 500

_EDGE = 64 * 1024


def fingerprint(target: Path) -> tuple[str, int] | None:
    """A cheap content fingerprint: size, mtime and a hash of the first and
    last 64 KB. Returns (fingerprint, size), or None when unreadable."""
    try:
        st = target.stat()
        h = hashlib.blake2b(digest_size=16)
        h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        with target.open("rb") as fh:
            h.update(fh.read(_EDGE))
            if st.st_size > _EDGE:
                fh.seek(max(_EDGE, st.st_size - _EDGE))
                h.update(fh.read(_EDGE))
    except OSError:
        return None
    return h.hexdigest(), st.st_size


def _root_for(target: Path, cwd: str | None) -> Path | None:
    """The repo whose .codegraph/ holds the record: the nearest one above the
    image, else the nearest one above the session's cwd, so an image outside
    the repo (a screenshot in /tmp) is still tracked."""
    starts = [target.parent]
    if cwd:
        starts.append(Path(cwd))
    for start in starts:
        for parent in [start, *start.parents]:
            if (parent / ".codegraph").is_dir():
                return parent
    return None


def _record_path(root: Path, session_id: str, kind: str = "images") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "unknown"
    return root / ".codegraph" / "sessions" / f"{kind}-{safe}.json"


def _load(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(path: Path, records: dict[str, dict]) -> None:
    if len(records) > MAX_ENTRIES:
        newest = sorted(records.items(), key=lambda kv: kv[1].get("at", 0))
        records = dict(newest[-MAX_ENTRIES:])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(records), encoding="utf-8")
    tmp.replace(path)


def hint_text(name: str, size: int, at: float) -> str:
    kb = max(1, round(size / 1024))
    when = time.strftime("%H:%M", time.localtime(at))
    return (
        f"[cgh hook] {name} was already read in this session ({kb} KB, at "
        f"{when}); it is still in the conversation above. Re-read it only if "
        "it changed."
    )


def reread_hint(payload: dict, target: Path) -> str | None:
    """Record this Read of an image and return the re-read hint when the
    same path with the same fingerprint was already read in this session.
    None for a non-image, a first or changed read, no session_id, or any
    error (an unwritable .codegraph/ included)."""
    try:
        if target.suffix.lower() not in IMAGE_EXTS:
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        if not target.is_file():
            return None
        cwd = payload.get("cwd")
        root = _root_for(target, cwd if isinstance(cwd, str) else None)
        if root is None:
            return None
        fp = fingerprint(target)
        if fp is None:
            return None
        digest, size = fp
        key = str(target.resolve())
        path = _record_path(root, session_id)
        records = _load(path)
        prev = records.get(key)
        if prev and prev.get("fp") == digest:
            return hint_text(target.name, size, float(prev.get("at", time.time())))
        records.pop(key, None)
        records[key] = {"fp": digest, "size": size, "at": time.time()}
        _save(path, records)
    except Exception:
        return None
    return None


# A full Read of a text file below this costs less than the hint; above
# FULL_READ_ANSWER, answer mode denies it with its plan instead, and a
# denied Read must not be recorded as read.
TEXT_REREAD_MIN = 4 * 1024


def text_hint_text(name: str, size: int, at: float, other_agent: bool,
                   outline: str | None) -> str:  # fmt: skip
    kb = max(1, round(size / 1024))
    when = time.strftime("%H:%M", time.localtime(at))
    if not other_agent:
        return (
            f"[cgh hook] {name} was already read in full in this session ({kb} KB, "
            f"at {when}) and has not changed; it is still in the conversation above."
        )
    head = (
        f"[cgh hook] {name} ({kb} KB) was read in full by another agent of this "
        f"session at {when} and has not changed. Read only the part you need"
    )
    if outline:
        return f"{head}, with offset/limit:\n{outline}"
    return f"{head}: file_summary gives its outline with line ranges."


def deny_text(name: str, size: int, at: float, plan: str) -> str:
    kb = max(1, round(size / 1024))
    when = time.strftime("%H:%M", time.localtime(at))
    return (
        f"[cgh] Full Read of {name} ({kb} KB) answered with its plan instead: "
        f"another agent of this session read it in full at {when} and it has "
        f"not changed.\n{plan}\nRead the part you need with offset/limit. If "
        "you need the whole file, repeat the same Read and it goes through."
    )


def text_reread_hint(
    payload: dict, target: Path, answers: bool = False
) -> tuple[str, bool] | None:
    """Record this full Read of a text file and, when the same unchanged file
    was already read in full in this session, return (text, deny): the same
    agent is told it still has it; another agent gets the outline, as a note,
    or with ``answers`` as a deny carrying the plan (its repeat goes
    through, and a denied Read is not recorded). None for an image, a file
    outside TEXT_REREAD_MIN..FULL_READ_ANSWER, a binary, a first or changed
    read, no session_id, or any error."""
    from codegraph.cli import hook_answers

    try:
        if target.suffix.lower() in IMAGE_EXTS:
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        if not target.is_file():
            return None
        size = target.stat().st_size
        if not TEXT_REREAD_MIN <= size <= hook_answers.FULL_READ_ANSWER:
            return None
        if hook_answers.is_binary(target):
            return None
        cwd = payload.get("cwd")
        root = _root_for(target, cwd if isinstance(cwd, str) else None)
        if root is None:
            return None
        fp = fingerprint(target)
        if fp is None:
            return None
        digest, size = fp
        agent = payload.get("agent_id")
        agent = agent if isinstance(agent, str) else ""
        key = str(target.resolve())
        path = _record_path(root, session_id, "reads")
        records = _load(path)
        prev = records.get(key)
        if prev and prev.get("fp") == digest:
            other = str(prev.get("agent", "")) != agent
            at = float(prev.get("at", time.time()))
            outline = (
                hook_answers.outline_answer(root, target, tail=False) if other else None
            )
            if other and answers:
                # One deny per agent: its repeat goes through, and another
                # agent reading the same file is answered again.
                denied = f"{key}#agent={agent}"
                if hook_answers.retry_after_deny(root, session_id, denied):
                    return None
                if hook_answers.remember_deny(root, session_id, denied):
                    plan = outline or hook_answers.text_outline(target, size)
                    return deny_text(target.name, size, at, plan), True
            return text_hint_text(target.name, size, at, other, outline), False
        records.pop(key, None)
        records[key] = {"fp": digest, "size": size, "at": time.time(), "agent": agent}
        _save(path, records)
    except Exception:
        return None
    return None
