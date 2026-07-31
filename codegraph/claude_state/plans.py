# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Scan Claude Code plan files into the FTS index.
#              Default location is ~/.claude/plans/, overridable via
#              CG_PLANS_DIR env or [paths] in config.toml.

from __future__ import annotations

import re
from pathlib import Path

# Filenames look like:
#   nifty-popping-sprout.md                          → slug, no agent_id
#   crispy-dancing-wombat-agent-a621be379d1d9a330.md → slug + agent_id
_AGENT_SUFFIX = re.compile(r"^(?P<slug>.+?)-agent-(?P<agent>[a-f0-9]+)$", re.IGNORECASE)


def parse_filename(stem: str) -> tuple[str, str]:
    """Return (slug, agent_id). agent_id is empty when the plan has no suffix."""
    m = _AGENT_SUFFIX.match(stem)
    if m:
        return m.group("slug"), m.group("agent")
    return stem, ""


def extract_title(text: str) -> str:
    """First H1 heading; fall back to the first non-empty stripped line."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s.lstrip("# ").strip()
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:120]
    return ""


def scan_plan_dir(repo_root: str | Path, verbose: bool = False) -> dict:
    """
    Scan the plans directory and upsert every .md into the FTS index.
    mtime-based skip; removes entries whose files no longer exist.

    Returns a summary dict.
    """
    from codegraph.core.config import plans_dir as _plans_dir
    from codegraph.core.fts import (
        delete_plan_entry,
        get_fts_conn,
        upsert_plan_entry,
    )

    pdir = _plans_dir(repo_root)
    stats = {
        "plans_dir": str(pdir),
        "indexed": 0,
        "skipped": 0,
        "removed": 0,
    }
    if not pdir.exists() or not pdir.is_dir():
        return stats

    conn = get_fts_conn(repo_root)
    existing: dict[str, float] = dict(conn.execute("SELECT path, mtime FROM plan_entries").fetchall())

    seen: set[str] = set()
    for path in sorted(pdir.glob("*.md")):
        seen.add(str(path))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if abs(existing.get(str(path), 0.0) - mtime) < 0.01:
            stats["skipped"] += 1
            continue

        slug, agent_id = parse_filename(path.stem)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = extract_title(text)
        upsert_plan_entry(conn, str(path), slug, agent_id, title, text, mtime)
        stats["indexed"] += 1
        if verbose:
            tag = f" [agent {agent_id[:8]}]" if agent_id else ""
            print(f"  + {path.name}{tag}")

    for path in list(existing):
        if path not in seen:
            delete_plan_entry(conn, path)
            stats["removed"] += 1

    conn.commit()

    try:
        from codegraph.state.activity import log as _log

        _log(
            repo_root,
            "plan_scan",
            f"indexed={stats['indexed']} skipped={stats['skipped']} removed={stats['removed']}",
        )
    except Exception:
        pass

    return stats
