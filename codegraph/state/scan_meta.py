# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Scan metadata: tags each indexing run with the git HEAD + branch.
#              Lets tools like `cgh stats`, `scan_status` MCP tool, and skills
#              decide whether the graph is fresh relative to the working tree.

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from codegraph.core.utils import quiet_subprocess_kwargs

_META_FILE = "scan_meta.json"

# Version of what a completed scan leaves in the graph. Bump it when a release
# changes what indexing stores so an existing index is only correct after
# every file is parsed again: the next index (CLI, owner start, incremental)
# then re-parses the whole repo once. A meta file without the key predates
# versioning and counts as 1.
#   2: call sites are persisted (call_site table) to resolve CALLS edges
#      independently of file order and across reindexes of the callee file.
#   3: the other by-name references (class bases, markdown mentions and
#      links, cross-file endpoint handlers) are persisted (name_ref table)
#      for the same reason.
#   4: Terraform is parsed into addressed blocks (data, module, locals,
#      providers and .tfvars entries too) with reference edges between them.
#   5: call sites carry their shape (bare, self, module, attribute) and the
#      import behind the name, and CALLS edges follow it instead of linking
#      every function of the called name.
GRAPH_FORMAT = 5
# Files written by an indexer that predates the per-file stamps (file_stamp
# table) are found and parsed again one by one; the stamps needed no bump of
# their own.


def _meta_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _META_FILE


def _git(repo_root: str | Path, *args: str) -> str | None:
    """Run `git <args>` in repo_root. Return stripped stdout or None on failure."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(repo_root),
            timeout=5,
            **quiet_subprocess_kwargs(),
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


def _kept_stats(repo_root: Path, stats: dict) -> dict:
    """The subset of scan stats worth persisting.

    Import coverage is carried over when a run recorded none: an incremental
    scan that re-parses nothing would otherwise erase the measurement taken by
    the last full scan, and `cgh status` would go back to saying nothing.
    """
    kept = {
        k: v
        for k, v in stats.items()
        if k
        in (
            "indexed",
            "skipped",
            "errors",
            "elapsed_s",
            "method",
            "extra_dirs",
            "imports",
            "imports_partial",
        )
    }
    # A run that short-circuits unchanged files measures only what it parsed.
    # Letting that smaller number land would replace a full measurement with a
    # partial one, and nothing downstream could tell.
    previous_stats = (read_meta(repo_root) or {}).get("stats") or {}
    previous = previous_stats.get("imports")
    previous_partial = bool(previous_stats.get("imports_partial"))
    supersedes = kept.get("imports_partial") and not previous_partial
    if previous and (not kept.get("imports") or supersedes):
        kept["imports"] = previous
        kept["imports_partial"] = previous_partial
    return kept


def write_meta(repo_root: str | Path, stats: dict) -> None:
    """Persist scan metadata after index_repo completes."""
    repo_root = Path(repo_root)
    meta = {
        "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Absolute root the index was built at. Graph node paths are stored
        # absolute, so an index copied or moved to a different path (a repo
        # zipped from another machine, a fresh clone at a new location) points
        # every result at the old root. Recording it lets the incremental path
        # detect the move and force a full rebuild instead of trusting the
        # unchanged git blob shas and keeping the stale paths.
        "root": str(repo_root.resolve()),
        "git_head": current_git_head(repo_root),
        "git_branch": current_git_branch(repo_root),
        "stats": _kept_stats(repo_root, stats),
        "graph_format": GRAPH_FORMAT,
    }
    try:
        path = _meta_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def clear_meta(repo_root: str | Path) -> None:
    """Forget the last scan, so scan_status stops reporting the index fresh.
    Used when the graph was wiped and has to be rebuilt."""
    try:
        _meta_path(repo_root).unlink(missing_ok=True)
    except OSError:
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


def graph_format_outdated(repo_root: str | Path) -> bool:
    """True when the last completed scan was written by an older graph
    format and the graph needs one full re-parse. No scan record at all is
    not outdated: that is a fresh or interrupted index, which the caller
    already handles as a full scan."""
    meta = read_meta(repo_root)
    if meta is None:
        return False
    try:
        return int(meta.get("graph_format", 1)) < GRAPH_FORMAT
    except (TypeError, ValueError):
        return True


def recorded_graph_format(meta: dict | None) -> int | None:
    """The graph format a scan record was written in: None without a record,
    1 for a record that predates versioning, 0 when unreadable (outdated)."""
    if meta is None:
        return None
    try:
        return int(meta.get("graph_format", 1))
    except (TypeError, ValueError):
        return 0


def format_notice(ss: dict) -> str | None:
    """One line on an index older than this cgh's graph format, from a
    scan_status() result, or None when the format is current."""
    if not ss.get("format_outdated"):
        return None
    old, cur = ss.get("graph_format"), ss.get("graph_format_current")
    if ss.get("indexing"):
        return (
            f"one-time re-parse running (index format {old}, this cgh writes "
            f"{cur}): answers may be incomplete until it ends"
        )
    return (
        f"index written in graph format {old}, this cgh needs {cur}: answers "
        "miss cross-file edges until the one-time re-parse. Run `cgh index` "
        "or start the owner"
    )


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
      dirty                                      (bool | None, working tree)
      behind_by                                  (int | None, commits)
      changed_files                              (list[str], since indexed_sha)
      fresh                                      (bool, no drift and the
                                                  graph format is current)
      indexing                                   ({pid, since} | None)
      graph_format, graph_format_current,
      format_outdated                            (index older than this cgh)
      state      fresh | stale | outdated | reindexing | indexing |
                 indexed | none

    ``indexing`` is set while an index of this repo is running. The metadata
    is only written when an index completes, so during a first index
    indexed_sha is null although the store is being built; without this flag
    that read as "never indexed".
    """
    from codegraph.state.index_lock import holder

    root = Path(repo_root)
    running = holder(root)
    indexing = (
        {
            "pid": running["pid"],
            "since": datetime.fromtimestamp(running["since"], UTC).isoformat(
                timespec="seconds"
            ),
        }
        if running
        else None
    )
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
    # NOT stale, the watcher keeps the index in sync on each file save.
    # If the watcher is down, a separate check would be needed, but the
    # git-vs-index sha comparison alone is the right coarse signal.
    # An index written by an older graph format lacks edges only a re-parse
    # adds, so it is not fresh whatever its git HEAD says.
    recorded = recorded_graph_format(meta or None)
    outdated = recorded is not None and recorded < GRAPH_FORMAT
    fresh = (
        indexed_sha is not None
        and current_sha is not None
        and indexed_sha == current_sha
        and not outdated
    )
    if outdated:
        state = "reindexing" if indexing else "outdated"
    elif indexing:
        state = "indexing"
    elif fresh:
        state = "fresh"
    elif indexed_sha:
        state = "stale"
    else:
        state = "indexed" if meta.get("indexed_at") else "none"

    return {
        "state": state,
        "graph_format": recorded,
        "graph_format_current": GRAPH_FORMAT,
        "format_outdated": outdated,
        "imports": (meta.get("stats") or {}).get("imports") or {},
        "imports_partial": bool((meta.get("stats") or {}).get("imports_partial")),
        "indexed_sha": indexed_sha,
        "indexed_branch": indexed_branch,
        "indexed_at": indexed_at,
        "current_sha": current_sha,
        "current_branch": current_branch,
        "dirty": dirty,
        "behind_by": behind_by,
        "changed_files": changed,
        "fresh": fresh,
        "indexing": indexing,
    }
