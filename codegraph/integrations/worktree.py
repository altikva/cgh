# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Keep cgh's own footprint out of git inside a worktree. Running
#              `cgh init` in a git worktree writes machine-local tooling (the
#              agent usage block in CLAUDE.md/AGENTS.md, the MCP wiring, the cgh
#              skills and usage rule). On a sprint branch that already carries
#              them, those writes show up as committable changes an agent can
#              land by accident. hide_footprint does the housekeeping the
#              ondonne sprint script did by hand: mark the tracked files init
#              modified as skip-worktree, and add the untracked ones it created
#              to .git/info/exclude. Best-effort throughout: a git failure
#              leaves the files in place, it never aborts init. Only acts inside
#              a worktree (git-dir != git-common-dir); the main checkout is where
#              these files are meant to be committed and is left untouched.

from __future__ import annotations

import subprocess
from pathlib import Path

# The paths cgh init writes or edits. Files are classified per path; skill and
# rule dirs are matched by glob because their exact names depend on which
# skills shipped. .mcp.json and the settings files carry the MCP wiring.
_FOOTPRINT_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    ".mcp.json",
    ".cursor/rules/codegraph-usage.mdc",
    ".claude/settings.local.json",
    ".claude/settings.json",
    ".claude/rules/cgh-usage.md",
)
_FOOTPRINT_GLOBS = (
    ".claude/skills/cgh-*",
    ".cursor/rules/codegraph-*",
)


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    from codegraph.plugin_api import quiet_subprocess_kwargs

    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        **quiet_subprocess_kwargs(),
    )


def in_git_worktree(root: Path) -> bool:
    """True when ``root`` sits in a LINKED git worktree, not the main checkout.
    A worktree's git-dir (``.../.git/worktrees/<name>``) differs from its
    common dir (``.../.git``); in the main checkout they are the same."""
    out = _git(
        root, ["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"]
    )
    if out.returncode != 0:
        return False
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if len(lines) != 2:
        return False
    git_dir, common_dir = lines
    return Path(git_dir).resolve() != Path(common_dir).resolve()


def _exclude_file(root: Path) -> Path | None:
    out = _git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(out.stdout.strip()) / "info" / "exclude"


def _footprint_relpaths(root: Path) -> list[str]:
    """The cgh-managed paths that actually exist under ``root``, repo-relative."""
    found: list[str] = []
    for rel in _FOOTPRINT_FILES:
        if (root / rel).exists():
            found.append(rel)
    for pattern in _FOOTPRINT_GLOBS:
        parent = root / str(Path(pattern).parent)
        name_glob = Path(pattern).name
        if parent.is_dir():
            for match in sorted(parent.glob(name_glob)):
                found.append(str(match.relative_to(root)))
    return found


def _path_status(root: Path, rel: str) -> str:
    """ "tracked-modified", "untracked", or "clean" for a single path, read from
    git. "clean" covers unmodified-tracked, gitignored, and anything git will
    not report, none of which needs hiding."""
    out = _git(root, ["status", "--porcelain", "--ignored", "--", rel])
    if out.returncode != 0:
        return "clean"
    line = next((ln for ln in out.stdout.splitlines() if ln.strip()), "")
    if not line:
        return "clean"
    code = line[:2]
    if code == "??":
        return "untracked"
    if code in ("!!",):
        return "clean"  # already ignored, nothing to hide
    if "M" in code or "A" in code:
        return "tracked-modified"
    return "clean"


def _add_excludes(root: Path, patterns: list[str]) -> None:
    """Append gitignore patterns to the worktree's shared info/exclude, once."""
    path = _exclude_file(root)
    if path is None:
        return
    try:
        existing = (
            set(path.read_text(encoding="utf-8").splitlines())
            if path.exists()
            else set()
        )
        new = [p for p in patterns if p not in existing]
        if not new:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            if existing and not _ends_with_newline(path):
                fh.write("\n")
            fh.write("\n".join(new) + "\n")
    except OSError:
        return


def _ends_with_newline(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return True
    return not data or data.endswith(b"\n")


def hide_footprint(root: str | Path, *, enabled: bool = True) -> list[str]:
    """Keep cgh init's own writes out of git when run inside a worktree.

    Marks the tracked files init modified as skip-worktree and adds the
    untracked ones it created to the worktree's info/exclude. Returns the
    repo-relative paths it hid (empty when disabled, outside a worktree, or
    with nothing to hide). Best-effort: any git error is swallowed so init is
    never blocked by this housekeeping.
    """
    root = Path(root).resolve()
    if not enabled or not in_git_worktree(root):
        return []

    hidden: list[str] = []
    to_exclude: list[str] = []
    for rel in _footprint_relpaths(root):
        status = _path_status(root, rel)
        if status == "tracked-modified":
            res = _git(root, ["update-index", "--skip-worktree", rel])
            if res.returncode == 0:
                hidden.append(rel)
        elif status == "untracked":
            to_exclude.append("/" + rel)
            hidden.append(rel)
    if to_exclude:
        _add_excludes(root, to_exclude)
    return hidden
