# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Activity log: append-only file tracking indexer/watcher events
#              so `cgh tail` can surface live progress even when the indexer
#              is running inside an MCP server process the user can't see.

from __future__ import annotations

import time
from pathlib import Path

_ACTIVITY_FILE = "activity.log"
_MAX_LINES = 2000  # truncate when file grows past this


def _log_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _ACTIVITY_FILE


def log(repo_root: str | Path | None, event: str, detail: str = "") -> None:
    """
    Append an event to the activity log. Non-fatal, never raises.
    Format: TAB-separated "<unix_ts>\t<event>\t<detail>"
    """
    if repo_root is None:
        return
    try:
        path = _log_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = f"{time.time():.3f}\t{event}\t{detail}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def tail(repo_root: str | Path, n: int = 50) -> list[tuple[float, str, str]]:
    """Return the last n activity entries as (ts, event, detail) tuples."""
    path = _log_path(repo_root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-n:]
        out: list[tuple[float, str, str]] = []
        for line in lines:
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                try:
                    ts = float(parts[0])
                except ValueError:
                    continue
                event = parts[1]
                detail = parts[2] if len(parts) > 2 else ""
                out.append((ts, event, detail))
        return out
    except Exception:
        return []


def rotate_if_needed(repo_root: str | Path) -> None:
    """If the log exceeds _MAX_LINES, keep only the most recent half."""
    path = _log_path(repo_root)
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LINES:
            kept = lines[-(_MAX_LINES // 2) :]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception:
        pass
