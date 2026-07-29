# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Git-history churn analysis. Pure functions over `git log`, no
#              MCP and no graph DB. file_churn aggregates per-file commit
#              counts, recency, authors, and line deltas from the numstat
#              log. file_ownership rolls up the top authors for one file.
#              Results are cached per (repo_root, git HEAD) so a long-running
#              owner process does not re-shell out on every query. Every
#              git call has a timeout and degrades to an empty result when
#              git is absent or the command fails.

from __future__ import annotations

from codegraph.core.utils import quiet_subprocess_kwargs

import subprocess
from pathlib import Path

# How many commits back we walk by default. git log is slow on large repos,
# so we bound the history. Callers see this cap in the tool `note`.
DEFAULT_COMMIT_CAP = 2000

# Per-file ownership log is cheaper (one path), but still bounded.
OWNERSHIP_COMMIT_CAP = 500

_GIT_TIMEOUT = 30

# Cache keyed by (resolved_repo_root, head_sha, since). Keeps a long-lived
# owner process from re-running git log on every hotspots call. head_sha is
# part of the key so a new commit invalidates the entry naturally.
_CHURN_CACHE: dict[tuple[str, str | None, str | None], dict[str, dict]] = {}
_OWNERSHIP_CACHE: dict[tuple[str, str | None, str], list[dict]] = {}


def _git(repo_root: str | Path, *args: str) -> str | None:
    """Run `git <args>` in repo_root. Return raw stdout or None on failure.
    Mirrors the subprocess style in state/scan_meta.py."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(repo_root),
            timeout=_GIT_TIMEOUT,
            **quiet_subprocess_kwargs(),
        )
        if r.returncode == 0:
            return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _head_sha(repo_root: str | Path) -> str | None:
    out = _git(repo_root, "rev-parse", "HEAD")
    return out.strip() if out else None


def file_churn(
    repo_root: str | Path,
    since: str | None = None,
    commit_cap: int = DEFAULT_COMMIT_CAP,
) -> dict[str, dict]:
    """Aggregate per-file change history from the git log.

    Walks `git log --numstat` over at most `commit_cap` commits (or a
    `since` window, e.g. "3 months ago" or a ref), and returns a mapping of
    repo-relative file path to:

      commits        number of commits that touched the file
      last_modified  max author-time seen (unix seconds, int)
      authors        {author_name: commit_count} that touched the file
      lines_added    total added lines (binary diffs skipped)
      lines_deleted  total deleted lines

    Returns {} when the path is not a git repo, git is unavailable, or the
    command fails. Results are cached per (repo_root, HEAD, since).
    """
    root = Path(repo_root).resolve()
    head = _head_sha(root)
    key = (str(root), head, since)
    cached = _CHURN_CACHE.get(key)
    if cached is not None:
        return cached

    args = ["log", "--no-merges", "--numstat", "--format=commit\x1f%H\x1f%an\x1f%at"]
    if since:
        args.append(f"--since={since}")
    else:
        args.append(f"--max-count={int(commit_cap)}")

    out = _git(root, *args)
    if out is None:
        # Do not cache a transient failure: a later call might succeed.
        return {}

    result: dict[str, dict] = {}
    cur_author = ""
    cur_time = 0
    for line in out.splitlines():
        if not line:
            continue
        if line.startswith("commit\x1f"):
            parts = line.split("\x1f")
            # parts = ["commit", sha, author, author_time]
            cur_author = parts[2] if len(parts) > 2 else ""
            try:
                cur_time = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                cur_time = 0
            continue
        # numstat line: "<added>\t<deleted>\t<path>". Binary files show "-".
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        added_s, deleted_s, path = cols
        path = _strip_rename(path)
        if not path:
            continue
        entry = result.get(path)
        if entry is None:
            entry = {
                "commits": 0,
                "last_modified": 0,
                "authors": {},
                "lines_added": 0,
                "lines_deleted": 0,
            }
            result[path] = entry
        entry["commits"] += 1
        if cur_time > entry["last_modified"]:
            entry["last_modified"] = cur_time
        if cur_author:
            entry["authors"][cur_author] = entry["authors"].get(cur_author, 0) + 1
        if added_s != "-":
            try:
                entry["lines_added"] += int(added_s)
            except ValueError:
                pass
        if deleted_s != "-":
            try:
                entry["lines_deleted"] += int(deleted_s)
            except ValueError:
                pass

    _CHURN_CACHE[key] = result
    return result


def _strip_rename(path: str) -> str:
    """Normalise a numstat path. Git emits renames as either
    "old => new" or "dir/{old => new}/file"; we keep the new (current) path
    so the churn keys match the working tree."""
    if "=>" not in path:
        return path.strip()
    # Brace form: prefix{old => new}suffix
    if "{" in path and "}" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        new = mid.split("=>", 1)[1].strip()
        combined = f"{pre}{new}{post}".replace("//", "/")
        return combined.strip()
    # Plain form: old => new
    return path.split("=>", 1)[1].strip()


def file_ownership(
    repo_root: str | Path,
    file_path: str | Path,
    commit_cap: int = OWNERSHIP_COMMIT_CAP,
) -> list[dict]:
    """Top authors for a single file, by commit count then recency.

    Uses `git log --format=%an|%at -- <file>` (cheaper than blame and good
    enough for an ownership signal). `file_path` may be absolute or
    repo-relative; git resolves it against the repo. Returns a list of
    {name, commits, last_commit} sorted by commit count descending, then by
    most recent commit. Returns [] on any failure or when git is absent.
    Cached per (repo_root, HEAD, file).
    """
    root = Path(repo_root).resolve()
    head = _head_sha(root)
    # Normalise to a repo-relative POSIX path when the file lives under the
    # repo, so absolute and relative callers share a cache entry.
    rel = _to_repo_relative(root, file_path)
    key = (str(root), head, rel)
    cached = _OWNERSHIP_CACHE.get(key)
    if cached is not None:
        return cached

    out = _git(
        root,
        "log",
        f"--max-count={int(commit_cap)}",
        "--format=%an\x1f%at",
        "--",
        rel,
    )
    if out is None:
        return []

    tally: dict[str, dict] = {}
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        name = parts[0] if parts else ""
        if not name:
            continue
        try:
            ts = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            ts = 0
        rec = tally.get(name)
        if rec is None:
            tally[name] = {"name": name, "commits": 1, "last_commit": ts}
        else:
            rec["commits"] += 1
            if ts > rec["last_commit"]:
                rec["last_commit"] = ts

    ranked = sorted(
        tally.values(),
        key=lambda r: (r["commits"], r["last_commit"]),
        reverse=True,
    )
    _OWNERSHIP_CACHE[key] = ranked
    return ranked


def _to_repo_relative(root: Path, file_path: str | Path) -> str:
    """Return a POSIX repo-relative path when file_path is under root,
    otherwise return the path as given (git handles the rest)."""
    p = Path(file_path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return str(file_path)
    return Path(file_path).as_posix()


def clear_cache() -> None:
    """Drop the per-HEAD churn / ownership caches. Mostly for tests."""
    _CHURN_CACHE.clear()
    _OWNERSHIP_CACHE.clear()
