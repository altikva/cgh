# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read a git repo at a named ref without checking it out:
#              grep_at_ref (git grep on a commit) and read_at_ref (one file,
#              a line range). The repo is this project or one of the sibling
#              repos declared under [codegraph] siblings; resolve_repo refuses
#              anything else. Refs go through the same validation as pinned
#              Terraform module refs, and nothing ever reaches the network.

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codegraph.analysis.pattern import _MAX_LINE_LEN, PatternHit, _expand_braces
from codegraph.analysis.terraform import git_toplevel, resolve_ref
from codegraph.core.utils import quiet_subprocess_kwargs

_GREP_TIMEOUT = 30
_MAX_READ_LINES = 400
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


class RefError(ValueError):
    """A repo, ref or path that cannot be read; the message says why."""


@dataclass(frozen=True)
class RefTarget:
    """A repo to read at a commit: its name ("" for this project), its work
    tree, the ref as asked and the commit it resolves to."""

    name: str
    top: Path
    ref: str
    sha: str


def sibling_repos(repo_root: str | Path) -> dict[str, Path]:
    """{name: work tree} of the git repos under [codegraph] siblings, named
    by their directory. An entry that is not a git work tree is left out."""
    from codegraph.core.config import load_config

    root = Path(repo_root)
    out: dict[str, Path] = {}
    for raw in load_config(root).siblings:
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = root / p
        top = git_toplevel(str(p.resolve()))
        if top:
            out.setdefault(Path(top).name, Path(top))
    return out


def resolve_repo(repo_root: str | Path, repo: str) -> tuple[str, Path]:
    """(name, work tree) of the repo a ref query targets: this project for
    "", else a declared sibling, by name or by path."""
    root = Path(repo_root)
    if not repo:
        top = git_toplevel(str(root.resolve()))
        if not top:
            raise RefError(f"{root} is not a git repository")
        return "", Path(top)
    siblings = sibling_repos(root)
    if repo in siblings:
        return repo, siblings[repo]
    try:
        wanted = Path(os.path.expanduser(repo)).resolve()
    except OSError:
        wanted = None
    for name, top in siblings.items():
        if wanted == top:
            return name, top
    known = ", ".join(sorted(siblings)) or "none declared"
    raise RefError(
        f"repo {repo!r} is not a declared sibling ({known}); add it under "
        "[codegraph] siblings in .codegraph/config.toml"
    )


def resolve_target(repo_root: str | Path, repo: str, ref: str) -> RefTarget:
    """The repo and commit a ref query reads. The ref is a branch, tag,
    remote-tracking branch (origin/develop) or commit id; a bare branch
    name also matches origin/<name>. Never fetches."""
    name, top = resolve_repo(repo_root, repo)
    sha = resolve_ref(str(top), ref)
    if not sha:
        where = name or "this repo"
        raise RefError(
            f"ref {ref!r} not found in {where} (no fetch is done: run "
            "`git fetch` there if it is a remote branch)"
        )
    return RefTarget(name=name, top=top, ref=ref, sha=sha)


def _run_git(top: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    return subprocess.run(
        ["git", "-C", str(top), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GREP_TIMEOUT,
        env=env,
        **quiet_subprocess_kwargs(),
    )


def grep_at_ref(
    target: RefTarget,
    pattern: str,
    glob: str = "",
    max_results: int = 50,
    regex: bool = True,
    case_sensitive: bool = False,
) -> list[PatternHit]:
    """git grep over the tree of ``target.sha``. Hit files are the paths
    inside the repo; read them with read_at_ref, not from disk."""
    if not pattern or not _SHA_RE.match(target.sha):
        return []
    args = ["grep", "-n", "-I", "--no-color"]
    if not case_sensitive:
        args.append("-i")
    args.append("-E" if regex else "-F")
    # "-e" so a pattern starting with "-" is never read as an option; the
    # commit is a validated sha, so it cannot be one either.
    args.extend(["-e", pattern, target.sha])
    if glob:
        args.extend(["--", *_expand_braces(glob)])
    try:
        r = _run_git(target.top, args)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RefError(f"git grep failed: {exc}") from exc
    if r.returncode not in (0, 1):  # 1 = no match
        raise RefError(f"git grep failed: {r.stderr.strip()[:200]}")
    prefix = f"{target.sha}:"
    out: list[PatternHit] = []
    for raw in r.stdout.splitlines():
        if not raw.startswith(prefix):
            continue
        parts = raw[len(prefix) :].split(":", 2)
        if len(parts) < 3:
            continue
        rel, ln, text = parts
        try:
            line_no = int(ln)
        except ValueError:
            continue
        out.append(PatternHit(file=rel, line=line_no, text=text[:_MAX_LINE_LEN]))
        if len(out) >= max_results:
            break
    return out


def _clean_path(path: str) -> str:
    """A path inside the repo tree, as git names it: relative, forward
    slashes, no "." or ".." part."""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    parts = [x for x in p.split("/") if x]
    if not parts or p.startswith("/") or any(x in (".", "..") for x in parts):
        raise RefError(f"path {path!r} must be relative to the repo root")
    return "/".join(parts)


def read_at_ref(
    target: RefTarget, path: str, start_line: int = 1, end_line: int = 0
) -> dict:
    """Lines ``start_line``..``end_line`` (1-based, inclusive; 0 = to the
    end) of ``path`` at ``target.sha``, capped at 400 lines."""
    rel = _clean_path(path)
    if not _SHA_RE.match(target.sha):
        raise RefError("unresolved ref")
    try:
        r = _run_git(target.top, ["cat-file", "blob", f"{target.sha}:{rel}"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise RefError(f"git cat-file failed: {exc}") from exc
    if r.returncode != 0:
        raise RefError(f"{rel} does not exist at {target.ref}")
    lines = r.stdout.splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = total if end_line <= 0 else min(end_line, total)
    truncated = end - start + 1 > _MAX_READ_LINES
    if truncated:
        end = start + _MAX_READ_LINES - 1
    return {
        "path": rel,
        "total_lines": total,
        "start_line": start,
        "end_line": end,
        "truncated": truncated,
        "lines": [
            {"line": i, "text": lines[i - 1][: _MAX_LINE_LEN * 5]}
            for i in range(start, end + 1)
        ],
    }
