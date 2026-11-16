# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read a git repo at a named ref without checking it out:
#              grep_at_ref (git grep on a commit), read_at_ref (one file,
#              a line range) and outline_of (its symbols, parsed from the
#              text at that commit). The repo is this project, a sibling
#              declared under [cgh] siblings (the user's global
#              ~/.cgh/config.toml or the project's own .cgh/config.toml), or
#              a subrepo this workspace federates; resolve_repo refuses
#              anything else. Refs go through the same validation as pinned
#              Terraform module refs, and nothing ever reaches the network.

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cgh.analysis.pattern import (
    _MAX_LINE_LEN,
    PatternHit,
    git_pathspecs,
    git_regex_args,
)
from cgh.analysis.terraform import git_env, git_toplevel, resolve_ref
from cgh.core.config import state_dir_for
from cgh.core.utils import quiet_subprocess_kwargs

_log = logging.getLogger(__name__)
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


_GLOBAL_WHERE = "~/.cgh/config.toml"
_PROJECT_WHERE = "this project's .cgh/config.toml"


def _siblings_from(path: Path) -> list[str]:
    """The [cgh] siblings list of one TOML file, [] when absent."""
    from cgh.core.config import _read_toml

    raw = _read_toml(path).get("cgh", {}).get("siblings", [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


def declared_global_siblings() -> list[str]:
    """The [cgh] siblings entries of the user's global config
    (~/.cgh/config.toml, with the legacy ~/.codegraph/ read fallback)."""
    from cgh.core.config import global_config_path

    return _siblings_from(global_config_path())


# Kept as the former public name (global siblings) for any external caller.
declared_siblings = declared_global_siblings


def declared_project_siblings(root: str | Path) -> list[str]:
    """The [cgh] siblings entries of a project's own .cgh/config.toml."""
    from cgh.core.config import CONFIG_FILE

    return _siblings_from(state_dir_for(Path(root)) / CONFIG_FILE)


def _git_top(p: Path) -> Path | None:
    """The git work tree holding ``p``, or None when it is not a git repo."""
    try:
        resolved = p.resolve()
    except OSError:
        return None
    top = git_toplevel(str(resolved))
    return Path(top) if top else None


def _has_local_index(top: Path) -> bool:
    """True when ``top`` carries its own cgh index (a .cgh/ or legacy
    .codegraph/ dir). A project-declared repo is only readable when it is
    indexed locally, the same condition federation already requires, so an
    entry can never point at an arbitrary directory (``~/.ssh``), only at a
    repo the machine owner has deliberately indexed."""
    from cgh.core.config import CGH_DIR, LEGACY_CGH_DIR

    return (top / CGH_DIR).is_dir() or (top / LEGACY_CGH_DIR).is_dir()


def _within(path: Path, boundary: Path) -> bool:
    """True when ``path`` sits inside ``boundary`` (inclusive), comparing
    resolved paths so a /var vs /private/var symlink does not matter."""
    try:
        p, b = path.resolve(), boundary.resolve()
    except OSError:
        return False
    try:
        return p == b or p.is_relative_to(b)
    except ValueError:
        return False


def _project_config_trusted(root: Path) -> bool:
    """Whether a project's own .cgh/config.toml may grant repo read access.

    Only a LOCAL, untracked config is trusted. .cgh/ is gitignored by
    default, so an untracked config is the machine owner's; a tracked or
    committed one can ride in with a cloned or opened repo and point cgh at
    arbitrary paths (``~/.ssh``, another repo) to exfiltrate files, a
    supply-chain / IDOR escalation. A tracked config's siblings/subrepos are
    ignored with a warning; the global ~/.cgh/config.toml stays the trusted
    source. A config that resolves outside the repo (a planted symlink) is
    refused too."""
    from cgh.core.config import CONFIG_FILE

    cfg = state_dir_for(root) / CONFIG_FILE
    if not cfg.exists():
        return False
    try:
        rel = cfg.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    try:
        proc = _run_git(root, ["ls-files", "--error-unmatch", str(rel)])
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode == 0:  # the config is tracked by git
        _log.warning(
            "ignoring [cgh] siblings/subrepos in %s: a git-tracked project "
            "config does not grant access to other repos; declare them in the "
            "global %s instead",
            cfg,
            _GLOBAL_WHERE,
        )
        return False
    return True


def sibling_repos(repo_root: str | Path | None = None) -> dict[str, Path]:
    """{name: work tree} of the git repos a ref query may read, named by
    their directory. Three sources are unified here:

    - the user's global ~/.cgh/config.toml [cgh] siblings: the trusted
      source, absolute entries only (a relative one would name a different
      repo for every project). No extra condition.
    - the project's own .cgh/config.toml [cgh] siblings (relative entries
      resolved against the project root);
    - the project's federated subrepos ([cgh] subrepos): a workspace that
      federates a repo already reads its whole graph, so reading its files
      at a ref adds no access. This is what lets a coordination checkout
      see the repos it federates without declaring them twice.

    The two PROJECT-level sources are guarded, because a project config can
    arrive inside an opened repo: they are honoured only when the project
    config is local and untracked (`_project_config_trusted`), and each entry
    only when its repo is indexed locally (`_has_local_index`) and resolves
    inside this workspace (`_within` the project's parent). This keeps the
    managers' per-workspace federation working while refusing a config that
    points cgh at ~/.ssh or an unrelated repo.

    ``repo_root`` is this project's root; without it only the global
    siblings are returned. A non-git entry is left out. On a name clash an
    explicit sibling (global, then project) wins over a federated subrepo."""
    out: dict[str, Path] = {}

    for raw in declared_global_siblings():
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            _log.warning("ignoring relative sibling %r in %s", raw, _GLOBAL_WHERE)
            continue
        top = _git_top(p)
        if top is not None:
            out.setdefault(top.name, top)

    if repo_root is None:
        return out

    root = Path(repo_root).resolve()
    if not _project_config_trusted(root):
        return out

    boundary = root.parent

    def _add_guarded(p: Path, source: str) -> None:
        top = _git_top(p)
        if top is None or top == root:
            return
        if not _within(top, boundary):
            _log.warning(
                "ignoring %s %s: %s is outside this workspace (%s)",
                source,
                p,
                top,
                boundary,
            )
            return
        if not _has_local_index(top):
            _log.warning("ignoring %s %s: %s has no local .cgh/ index", source, p, top)
            return
        out.setdefault(top.name, top)

    for raw in declared_project_siblings(root):
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = root / p
        _add_guarded(p, "project sibling")

    # The unification: federated subrepos are readable at a ref too.
    from cgh.analysis.federation import resolve_children

    for child in resolve_children(root):
        _add_guarded(child, "federated subrepo")

    return out


def resolve_repo(repo_root: str | Path, repo: str) -> tuple[str, Path]:
    """(name, work tree) of the repo a ref query targets: this project for
    "", else a declared sibling or a federated subrepo, by name or by path."""
    root = Path(repo_root).resolve()
    if not repo:
        top = git_toplevel(str(root))
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
        f"repo {repo!r} is not a declared sibling ({known}); declare it under "
        f"[cgh] siblings in {_GLOBAL_WHERE} or {_PROJECT_WHERE}, or federate it "
        f"as a subrepo of this workspace"
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
    env = git_env()
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
    # "-e" so a pattern starting with "-" is never read as an option; the
    # commit is a validated sha, so it cannot be one either. A regex ERE
    # cannot express, on a git without PCRE, goes to -E as written: there is
    # no other backend for a tree at a commit.
    rx_args = git_regex_args(pattern) if regex else ["-F", "-e", pattern]
    args.extend(rx_args or ["-E", "-e", pattern])
    args.append(target.sha)
    if glob:
        args.extend(["--", *git_pathspecs(glob)])
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


def blob_at_ref(target: RefTarget, path: str) -> tuple[str, str]:
    """(clean path, text) of ``path`` at ``target.sha``."""
    rel = _clean_path(path)
    if not _SHA_RE.match(target.sha):
        raise RefError("unresolved ref")
    try:
        r = _run_git(target.top, ["cat-file", "blob", f"{target.sha}:{rel}"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise RefError(f"git cat-file failed: {exc}") from exc
    if r.returncode != 0:
        raise RefError(f"{rel} does not exist at {target.ref}")
    return rel, r.stdout


def slice_lines(rel: str, text: str, start_line: int = 1, end_line: int = 0) -> dict:
    """Lines ``start_line``..``end_line`` (1-based, inclusive; 0 = to the
    end) of ``text``, capped at 400 lines. A range that starts past the
    end, or ends before it starts, is refused."""
    lines = text.splitlines()
    total = len(lines)
    start = max(1, start_line)
    if start_line < 0 or end_line < 0:
        raise RefError("start_line and end_line must be positive")
    if end_line and end_line < start:
        raise RefError(f"end_line {end_line} is before start_line {start}")
    if total and start > total:
        raise RefError(f"start_line {start} is past the end ({total} lines)")
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


def read_at_ref(
    target: RefTarget, path: str, start_line: int = 1, end_line: int = 0
) -> dict:
    """Lines ``start_line``..``end_line`` (1-based, inclusive; 0 = to the
    end) of ``path`` at ``target.sha``, capped at 400 lines."""
    rel, text = blob_at_ref(target, path)
    return slice_lines(rel, text, start_line, end_line)


# Formats whose parser reads a binary container: their text from git is not
# what the parser expects, so no outline is attempted for them.
_BINARY_DOC_EXTS = frozenset({".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"})


def outline_of(rel: str, text: str) -> list[dict] | None:
    """Symbols of ``text`` with their line ranges, parsed by the parser of
    ``rel``'s language: [{name, kind, start_line, end_line}] in line order,
    a method named Class.method. None when no parser handles the file or
    the parse fails."""
    import tempfile

    from cgh.parsers import get_parser_for_path

    name = Path(rel).name
    if Path(name).suffix.lower() in _BINARY_DOC_EXTS:
        return None
    parser = get_parser_for_path(name)
    if parser is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="cgh-ref-") as tmp:
            p = Path(tmp) / name
            p.write_text(text, encoding="utf-8")
            idx = parser.parse(p)
    except Exception as exc:  # a parser must not break a read
        _log.debug("outline of %s failed: %s", rel, exc)
        return None
    out: list[dict] = []
    for c in idx.classes:
        out.append(_entry(c.name, c.kind, c.start_line, c.end_line))
    for f in idx.functions:
        qual = f"{f.class_name}.{f.name}" if f.class_name else f.name
        out.append(_entry(qual, f.kind, f.start_line, f.end_line))
    for r in idx.resources:
        out.append(_entry(r.address or r.name, r.kind, r.start_line, r.end_line))
    for s in idx.sections:
        out.append(_entry(s.title, "section", s.start_line, s.end_line))
    out.sort(key=lambda e: (e["start_line"], e["name"]))
    return out


def _entry(name: str, kind: str, start: int, end: int) -> dict:
    return {
        "name": name,
        "kind": kind,
        "start_line": start,
        "end_line": max(start, end or start),
    }


def find_symbol(outline: list[dict], symbol: str) -> list[dict]:
    """The outline entries ``symbol`` names: an exact name (Class.method
    included) first, else the entries whose last dotted part matches."""
    exact = [e for e in outline if e["name"] == symbol]
    if exact:
        return exact
    tail = symbol.rsplit(".", 1)[-1]
    return [e for e in outline if e["name"].rsplit(".", 1)[-1] == tail]
