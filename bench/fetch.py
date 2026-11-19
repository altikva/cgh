# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Clone each pinned benchmark repo into .bench-cache/ and check out
#              its exact pinned SHA, then verify HEAD equals that SHA. A drifted
#              checkout aborts the run, so no metric is ever computed against a
#              tree other than the one named in repos.yaml. Cloned repos are
#              untrusted data: paths are passed as arguments, nothing in them
#              is executed, and the clone is a plain git mirror of the content.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bench._util import CACHE_DIR, BenchError, repo_checkout, repos_config

_TIMEOUT = 600

# Hooks off for every git op: a global core.hooksPath post-checkout that
# backgrounds `cgh _reindex_hook` would otherwise reindex the clone and grab
# its graph lock behind the harness's back.
_NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]


def _git(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *_NO_HOOKS, *args]
    if cwd is not None:
        cmd = ["git", "-C", str(cwd), *_NO_HOOKS, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)


def fetch_one(name: str, url: str, sha: str) -> Path:
    """Clone (once) and hard-check out ``sha``, then verify HEAD == sha."""
    dest = repo_checkout(name)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not (dest / ".git").is_dir():
        print(f"[fetch] cloning {url} -> {dest}")
        # A full clone keeps every parent SHA reachable, which tier 2 needs
        # when it checks out each merged PR's parent commit.
        res = _git("clone", url, str(dest))
        if res.returncode != 0:
            raise BenchError(f"clone of {url} failed:\n{res.stderr}")
    else:
        print(f"[fetch] reusing existing clone {dest}")

    # Make sure the pinned object is present even if the clone predates it.
    _git("fetch", "--quiet", "origin", cwd=dest)

    res = _git("checkout", "--force", sha, cwd=dest)
    if res.returncode != 0:
        raise BenchError(f"checkout {sha} failed in {dest}:\n{res.stderr}")

    head = _git("rev-parse", "HEAD", cwd=dest).stdout.strip()
    if head != sha:
        raise BenchError(
            f"HEAD of {dest} is {head}, expected pinned {sha}. Refusing to run "
            f"on a drifted checkout."
        )
    print(f"[fetch] {name} at {head} (verified)")
    return dest


def fetch_all() -> dict[str, Path]:
    """Fetch every repo in repos.yaml, verifying each pinned SHA."""
    cfg = repos_config()
    out: dict[str, Path] = {}
    for name, spec in cfg["repos"].items():
        out[name] = fetch_one(name, spec["url"], spec["sha"])
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        fetch_all()
    except BenchError as exc:
        print(f"[fetch] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
