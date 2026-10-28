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
#              advisory line when the same unchanged file comes back. It
#              never denies, and any failure returns None.

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


def _record_path(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "unknown"
    return root / ".codegraph" / "sessions" / f"images-{safe}.json"


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
