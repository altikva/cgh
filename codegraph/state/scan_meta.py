# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Scan metadata: tags each indexing run with the git HEAD + branch.
#              Lets tools like `cgh stats`, `scan_status` MCP tool, and skills
#              decide whether the graph is fresh relative to the working tree.

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

_META_FILE = "scan_meta.json"


def _meta_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _META_FILE


def _git(repo_root: str | Path, *args: str) -> str | None:
    """Run `git <args>` in repo_root. Return stripped stdout or None on failure."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def current_git_head(repo_root: str | Path) -> str | None:
    return _git(repo_root, "rev-parse", "HEAD")


def current_git_branch(repo_root: str | Path) -> str | None:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def git_is_dirty(repo_root: str | Path) -> bool | None:
    """True if working tree has uncommitted changes. None if git unavailable."""
    out = _git(repo_root, "status", "--porcelain")
    if out is None:
        return None
    return bool(out.strip())


def commits_between(repo_root: str | Path, base: str, head: str = "HEAD") -> int | None:
    """Number of commits in head that are not in base. None on failure."""
    out = _git(repo_root, "rev-list", "--count", f"{base}..{head}")
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def changed_files(repo_root: str | Path, base: str, head: str = "HEAD") -> list[str]:
    """List of files changed between base and head."""
    out = _git(repo_root, "diff", "--name-only", "--diff-filter=ACMR", base, head)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def write_meta(repo_root: str | Path, stats: dict) -> None:
    """Persist scan metadata after index_repo completes."""
    repo_root = Path(repo_root)
    meta = {
        "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_head": current_git_head(repo_root),
        "git_branch": current_git_branch(repo_root),
        "stats": {
            k: v for k, v in stats.items() if k in ("indexed", "skipped", "errors", "elapsed_s", "method", "extra_dirs")
        },
    }
    try:
        path = _meta_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def read_meta(repo_root: str | Path) -> dict | None:
    """Load scan metadata, or None if missing/invalid."""
    path = _meta_path(repo_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def git_tree_blob_shas(repo_root: str | Path) -> dict[str, str] | None:
    """
    Return {relative_path: blob_sha} for every file in HEAD.
    Uses `git ls-tree -r HEAD`. Returns None if git unavailable.
    """
    out = _git(repo_root, "ls-tree", "-r", "HEAD")
    if out is None:
        return None
    result: dict[str, str] = {}
    for line in out.splitlines():
        # Format: "<mode> <type> <sha>\t<path>"
        try:
            meta, path = line.split("\t", 1)
            parts = meta.split()
            if len(parts) >= 3 and parts[1] == "blob":
                result[path] = parts[2]
        except ValueError:
            continue
    return result


def git_hash_object(repo_root: str | Path, path: str | Path) -> str | None:
    """
    Compute git blob SHA for a file's current on-disk content (may differ
    from HEAD for dirty files). Returns None if git unavailable.
    """
    out = _git(repo_root, "hash-object", str(path))
    return out if out else None


def scan_status(repo_root: str | Path) -> dict:
    """
    Compute the freshness of the graph vs the working tree.
    Returns a dict with:
      indexed_sha, indexed_branch, indexed_at  (from meta)
      current_sha, current_branch               (live from git)
      dirty                                      (bool | None — working tree)
      behind_by                                  (int | None — commits)
      changed_files                              (list[str] — since indexed_sha)
      fresh                                      (bool — no drift)
    """
    root = Path(repo_root)
    meta = read_meta(root) or {}
    indexed_sha = meta.get("git_head")
    indexed_branch = meta.get("git_branch")
    indexed_at = meta.get("indexed_at")

    current_sha = current_git_head(root)
    current_branch = current_git_branch(root)
    dirty = git_is_dirty(root)

    behind_by: int | None = None
    changed: list[str] = []
    if indexed_sha and current_sha and indexed_sha != current_sha:
        behind_by = commits_between(root, indexed_sha, current_sha)
        changed = changed_files(root, indexed_sha, current_sha)

    # Fresh means the graph matches HEAD. Dirty (uncommitted changes) is
    # NOT stale — the watcher keeps the index in sync on each file save.
    # If the watcher is down, a separate check would be needed, but the
    # git-vs-index sha comparison alone is the right coarse signal.
    fresh = indexed_sha is not None and current_sha is not None and indexed_sha == current_sha

    return {
        "indexed_sha": indexed_sha,
        "indexed_branch": indexed_branch,
        "indexed_at": indexed_at,
        "current_sha": current_sha,
        "current_branch": current_branch,
        "dirty": dirty,
        "behind_by": behind_by,
        "changed_files": changed,
        "fresh": fresh,
    }
