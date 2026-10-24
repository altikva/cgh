# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Read a git repo at a named ref without checking it out:
#              grep_at_ref (git grep on a commit), read_at_ref (one file,
#              a line range) and outline_of (its symbols, parsed from the
#              text at that commit). The repo is this project or one of the sibling
#              repos declared under [codegraph] siblings of the user's global
#              ~/.codegraph/config.toml; resolve_repo refuses anything else. Refs go through the same validation as pinned
#              Terraform module refs, and nothing ever reaches the network.

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codegraph.analysis.pattern import _MAX_LINE_LEN, PatternHit, _expand_braces
from codegraph.analysis.terraform import git_env, git_toplevel, resolve_ref
from codegraph.core.utils import quiet_subprocess_kwargs

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


_WHERE = "~/.codegraph/config.toml"


def _siblings_from(path: Path) -> list[str]:
    """The [codegraph] siblings list of one TOML file, [] when absent."""
    from codegraph.core.config import _read_toml

    raw = _read_toml(path).get("codegraph", {}).get("siblings", [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


def declared_siblings() -> list[str]:
    """The sibling entries of the user's global config. Granting read access
    to other repos is the machine owner's decision, so it lives outside
    every repo: a project's own .codegraph/config.toml can come with a
    cloned repo, a copied store or a seeded worktree, and no check on it
    (tracked or not, case, links) says reliably who wrote it."""
    from codegraph.core.config import CONFIG_FILE, GLOBAL_DIR

    return _siblings_from(GLOBAL_DIR / CONFIG_FILE)


def _project_declares_siblings(root: Path) -> bool:
    from codegraph.core.config import CODEGRAPH_DIR, CONFIG_FILE

    return bool(_siblings_from(root / CODEGRAPH_DIR / CONFIG_FILE))


def sibling_repos() -> dict[str, Path]:
    """{name: work tree} of the declared sibling git repos, named by their
    directory. Entries must be absolute (or ~): a relative one would mean a
    different repo for every project. A non-git entry is left out."""
    out: dict[str, Path] = {}
    for raw in declared_siblings():
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            _log.warning("ignoring relative sibling %r in %s", raw, _WHERE)
            continue
        top = git_toplevel(str(p.resolve()))
        if top:
            out.setdefault(Path(top).name, Path(top))
    return out


def resolve_repo(repo_root: str | Path, repo: str) -> tuple[str, Path]:
    """(name, work tree) of the repo a ref query targets: this project for
    "", else a declared sibling, by name or by path."""
    root = Path(repo_root).resolve()
    if not repo:
        top = git_toplevel(str(root))
        if not top:
            raise RefError(f"{root} is not a git repository")
        return "", Path(top)
    siblings = sibling_repos()
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
    hint = (
        " ([codegraph] siblings in this project's .codegraph/config.toml is "
        "ignored: only the user's global config may grant access to other "
        "repos)"
        if _project_declares_siblings(root)
        else ""
    )
    raise RefError(
        f"repo {repo!r} is not a declared sibling ({known}); add its absolute "
        f"path under [codegraph] siblings in {_WHERE}{hint}"
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

    from codegraph.parsers import get_parser_for_path

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
