# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Install and remove git hooks that keep the cgh code graph fresh
#              after content-changing git operations. The file watcher catches
#              individual saves, but a `git pull`, `merge`, branch `checkout`,
#              or `rebase` rewrites many files at once and the watcher misses
#              them. These hooks run a backgrounded incremental reindex so the
#              graph stays accurate. Public helpers: install_git_hooks,
#              uninstall_git_hooks, git_hooks_status.

from __future__ import annotations

from codegraph.core.utils import quiet_subprocess_kwargs

import subprocess
from pathlib import Path

# post-merge   fires after `git pull` and `git merge`.
# post-checkout fires after `git checkout <branch>` / `git switch` (and file
#               checkouts, which the hook body filters out).
# post-rewrite fires after `git rebase` and `git commit --amend`.
HOOK_EVENTS = ("post-merge", "post-checkout", "post-rewrite")

_MARKER_BEGIN = "# >>> cgh reindex >>>"
_MARKER_END = "# <<< cgh reindex <<<"


def _git_dir(repo_root: Path) -> Path | None:
    """Resolve the repo's git dir, honoring worktrees. None if not a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            **quiet_subprocess_kwargs(),
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out:
        return None
    p = Path(out)
    return p if p.is_absolute() else (repo_root / p)


def _hooks_dir(repo_root: Path) -> Path | None:
    """Where hooks live. Honors core.hooksPath, else <git-dir>/hooks.

    Resolves the git dir FIRST: `git config --get core.hooksPath` reads the
    global config even outside a repo, so checking it before confirming we are
    in a repo would point a non-repo path at the user's global hooks dir.
    """
    git_dir = _git_dir(repo_root)
    if git_dir is None:
        return None
    try:
        configured = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **quiet_subprocess_kwargs(),
        ).stdout.strip()
    except FileNotFoundError:
        return None
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else (repo_root / p)
    return git_dir / "hooks"


def hooks_target_info(repo_root: Path | str) -> "tuple[Path | None, bool]":
    """Return (hooks_dir, is_shared).

    hooks_dir is None when this is not a git repo. is_shared is True when
    core.hooksPath points outside this repo, i.e. a global or shared hooks
    directory used by many repos. cgh refuses to silently write there: the
    reindex hook is repo-scoped, but a shared dir is the user's to manage.
    """
    hooks_dir = _hooks_dir(repo_root)
    if hooks_dir is None:
        return None, False
    try:
        hooks_dir.resolve().relative_to(Path(repo_root).resolve())
        return hooks_dir, False
    except ValueError:
        return hooks_dir, True


def _reindex_block(event: str) -> str:
    """The cgh-managed shell block for one hook event, marker-delimited so
    uninstall can find and remove exactly this section."""
    # post-checkout receives (prev_head, new_head, branch_flag). branch_flag
    # is 1 for a branch switch and 0 for a file checkout. Only reindex on a
    # branch switch, file checkouts are already seen by the watcher.
    guard = '[ "$3" = "1" ] || exit 0\n' if event == "post-checkout" else ""
    return (
        f"{_MARKER_BEGIN}\n"
        "# Keep the cgh code graph fresh after git changes file content.\n"
        "# Backgrounded incremental reindex, never blocks or fails the git op.\n"
        f"{guard}"
        "if command -v cgh >/dev/null 2>&1; then\n"
        '  ( cgh _reindex_hook --root "$(git rev-parse --show-toplevel)" '
        ">/dev/null 2>&1 & )\n"
        "fi\n"
        f"{_MARKER_END}\n"
    )


def _strip_block(text: str) -> str:
    """Remove any existing cgh-managed block from a hook file's text."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == _MARKER_BEGIN:
            skipping = True
            continue
        if line.strip() == _MARKER_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def install_git_hooks(
    repo_root: Path | str, events: tuple[str, ...] = HOOK_EVENTS
) -> list[str]:
    """Install (or refresh) the cgh reindex block in each hook.

    Chains onto an existing hook instead of clobbering it: the cgh block is
    appended below whatever is already there, between markers. Re-running is
    idempotent (the old block is stripped first). Returns the events whose
    hooks were written. Returns [] when the repo has no hooks dir (not a git
    repo).
    """
    root = Path(repo_root)
    hooks_dir = _hooks_dir(root)
    if hooks_dir is None:
        return []
    hooks_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for event in events:
        hook_path = hooks_dir / event
        existing = ""
        if hook_path.exists():
            existing = _strip_block(hook_path.read_text(encoding="utf-8")).rstrip("\n")
        if not existing:
            existing = "#!/bin/sh"
        elif not existing.startswith("#!"):
            existing = "#!/bin/sh\n\n" + existing
        body = existing + "\n\n" + _reindex_block(event)
        hook_path.write_text(body, encoding="utf-8")
        hook_path.chmod(0o755)
        written.append(event)
    return written


def uninstall_git_hooks(repo_root: Path | str) -> list[str]:
    """Remove the cgh block from each hook. If a hook is left with only a
    shebang (cgh created it), delete the file. Returns events touched."""
    root = Path(repo_root)
    hooks_dir = _hooks_dir(root)
    if hooks_dir is None:
        return []
    touched: list[str] = []
    for event in HOOK_EVENTS:
        hook_path = hooks_dir / event
        if not hook_path.exists():
            continue
        text = hook_path.read_text(encoding="utf-8")
        if _MARKER_BEGIN not in text:
            continue
        stripped = _strip_block(text).strip()
        if stripped in ("", "#!/bin/sh", "#!/usr/bin/env sh"):
            hook_path.unlink()
        else:
            hook_path.write_text(stripped + "\n", encoding="utf-8")
            hook_path.chmod(0o755)
        touched.append(event)
    return touched


def git_hooks_status(repo_root: Path | str) -> dict[str, bool]:
    """Map each hook event to whether the cgh block is installed."""
    root = Path(repo_root)
    hooks_dir = _hooks_dir(root)
    status: dict[str, bool] = {event: False for event in HOOK_EVENTS}
    if hooks_dir is None:
        return status
    for event in HOOK_EVENTS:
        hook_path = hooks_dir / event
        if hook_path.exists() and _MARKER_BEGIN in hook_path.read_text(
            encoding="utf-8"
        ):
            status[event] = True
    return status
