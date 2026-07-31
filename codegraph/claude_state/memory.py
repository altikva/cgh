# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Scan Claude Code memory files into the FTS index.
#              Default location is ~/.claude/projects/-<slug>/memory/,
#              overridable via CG_MEMORY_DIR env or [paths] in config.toml.

from __future__ import annotations

import re
from pathlib import Path

_KIND_PATTERN = re.compile(r"^(user|feedback|project|reference)(?:_|$)", re.IGNORECASE)
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (yaml_dict, body_without_frontmatter). Simple kv parser."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end() :]
    fm: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm, body


def classify(path: Path) -> tuple[str, str]:
    """
    Return (kind, title) inferred from filename + content.
    kind ∈ {user, feedback, project, reference, other}.
    """
    name = path.stem
    m = _KIND_PATTERN.match(name)
    kind = m.group(1).lower() if m else "other"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return kind, name

    fm, body = _parse_frontmatter(text)

    # Frontmatter wins when provided (matches the auto-memory convention)
    kind = (fm.get("type") or kind).lower().strip()

    # Title: prefer frontmatter `name`, then first H1, then filename
    title = fm.get("name") or ""
    if not title:
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s.lstrip("# ").strip()
                break
    if not title:
        title = name.replace("_", " ")

    return kind, title


def scan_memory_dir(repo_root: str | Path, verbose: bool = False) -> dict:
    """
    Scan the memory directory for this project, upsert every .md file into
    the FTS index. Skips unchanged files (mtime-based).

    Returns a summary dict: {indexed, skipped, removed, memory_dir}.
    """
    from codegraph.core.config import memory_dir as _memory_dir
    from codegraph.core.fts import (
        delete_memory_entry,
        get_fts_conn,
        upsert_memory_entry,
    )

    mem_dir = _memory_dir(repo_root)
    stats = {
        "memory_dir": str(mem_dir),
        "indexed": 0,
        "skipped": 0,
        "removed": 0,
    }
    if not mem_dir.exists() or not mem_dir.is_dir():
        return stats

    conn = get_fts_conn(repo_root)

    # Build mtime map from DB for skip logic
    existing: dict[str, float] = dict(
        conn.execute("SELECT path, mtime FROM memory_entries").fetchall()
    )

    seen: set[str] = set()
    for path in sorted(mem_dir.glob("*.md")):
        seen.add(str(path))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if abs(existing.get(str(path), 0.0) - mtime) < 0.01:
            stats["skipped"] += 1
            continue

        kind, title = classify(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, body = _parse_frontmatter(text)
        upsert_memory_entry(conn, str(path), kind, title, body, mtime)
        stats["indexed"] += 1
        if verbose:
            print(f"  + {path.name} [{kind}]")

    # Purge entries whose files no longer exist
    for path in list(existing):
        if path not in seen:
            delete_memory_entry(conn, path)
            stats["removed"] += 1

    conn.commit()

    # Activity + scan_meta breadcrumbs
    try:
        from codegraph.state.activity import log as _log

        _log(
            repo_root,
            "memory_scan",
            f"indexed={stats['indexed']} skipped={stats['skipped']} removed={stats['removed']}",
        )
    except Exception:
        pass

    return stats
