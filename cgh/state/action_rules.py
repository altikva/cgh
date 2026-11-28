# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Check an action against the rule lines of standing instructions.
#              A rule line is `cgh-rule: <refuse|ask|allow> <verb> [key=glob ...]`
#              in the body of a standing instruction. This module parses those
#              lines, normalises an action string (a shell command or a short
#              phrase) into `{verb, fields, raw}` actions, matches the two and
#              folds the verdict. No SQL, no MCP and no model: the caller hands
#              in the entries. `allowed` is never a default, it needs a
#              matching allow line; a silent store answers `no_rule`.

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

RULE_PREFIX = "cgh-rule:"

# Rule line verdict -> answer verdict.
_ANSWER = {"refuse": "refused", "ask": "ask", "allow": "allowed"}

# Strictest first. Over several actions the strictest verdict wins, so a
# command is `allowed` only when every one of its segments is.
VERDICT_ORDER: tuple[str, ...] = ("refused", "ask", "no_rule", "allowed")

# One `git rev-parse --abbrev-ref HEAD` reads the fields that come from the
# repository. Past this delay the field is absent, never an error.
GIT_BRANCH_TIMEOUT_S = 2.0

_REASON_CLIP = 600
_MAX_SCRIPT_DEPTH = 3

_VERB_RE = re.compile(r"^[A-Za-z0-9_.*?\-]+$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Rule:
    """One parsed rule line."""

    verdict: str  # refuse | ask | allow
    verb: str  # dotted verb, or a glob over it
    constraints: tuple[tuple[str, tuple[str, ...]], ...]
    line: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "verb": self.verb,
            "constraints": {k: list(v) for k, v in self.constraints},
            "line": self.line,
        }


@dataclass
class Action:
    """One normalised action."""

    verb: str
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""
    # Fields the action has but cgh could not determine (the branch of a bare
    # push when git cannot be read). Different from a field that does not
    # apply: an allow rule must not clear what a refusal might have covered.
    unknown: tuple[str, ...] = ()
    # The directory the action runs in, absolute, once a command line was
    # read: the caller's, or the one the command names (`cd <dir> &&`,
    # `git -C <dir>`). "" when the command went somewhere cgh cannot follow
    # (`cd "$DIR"`), None for an action that was not read from a command.
    where: str | None = None


# ---------------------------------------------------------------------------
# Rule lines
# ---------------------------------------------------------------------------


def parse_rule_lines(body: str) -> tuple[list[Rule], list[dict[str, str]]]:
    """Parse the rule lines of a body. Returns ``(rules, errors)``.

    A line that does not parse never rules: it is reported in ``errors`` as
    ``{line, error}`` and left out of ``rules``.
    """
    rules: list[Rule] = []
    errors: list[dict[str, str]] = []
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("cgh-rule"):
            continue
        if not line.startswith(RULE_PREFIX):
            errors.append({"line": line, "error": "expected 'cgh-rule:'"})
            continue
        parsed = _parse_rule(line)
        if isinstance(parsed, str):
            errors.append({"line": line, "error": parsed})
        else:
            rules.append(parsed)
    return rules, errors


def _parse_rule(line: str) -> Rule | str:
    tokens = line[len(RULE_PREFIX) :].split()
    if len(tokens) < 2:
        return "expected '<verdict> <verb> [key=glob ...]'"
    verdict, verb = tokens[0], tokens[1]
    if verdict not in _ANSWER:
        return f"unknown verdict {verdict!r}: use refuse, ask or allow"
    if not _VERB_RE.match(verb):
        return f"invalid verb {verb!r}"
    constraints: dict[str, tuple[str, ...]] = {}
    for tok in tokens[2:]:
        key, sep, glob = tok.partition("=")
        if not sep or not _KEY_RE.match(key):
            return f"expected key=glob, got {tok!r}"
        alts = tuple(a for a in glob.split("|") if a)
        if not alts:
            return f"empty glob for {key!r}"
        if key in constraints:
            return f"duplicate key {key!r}"
        constraints[key] = alts
    return Rule(verdict, verb, tuple(constraints.items()), line)


def reason_of(body: str, clip: int = _REASON_CLIP) -> str:
    """The body without its rule lines: the reason shown with a verdict."""
    kept = [
        ln for ln in (body or "").splitlines() if not ln.strip().startswith("cgh-rule")
    ]
    text = "\n".join(kept).strip()
    if len(text) > clip:
        text = text[: clip - 3].rstrip() + "..."
    return text


def rule_matches(rule: Rule, action: Action) -> bool:
    """Verb glob and every field constraint match. A key the action does not
    carry never matches."""
    if not fnmatchcase(action.verb, rule.verb):
        return False
    for key, alts in rule.constraints:
        value = action.fields.get(key)
        if value is None or not any(fnmatchcase(value, a) for a in alts):
            return False
    return True


def _withheld_by_unknown(rule: Rule, action: Action) -> bool:
    """A refuse or ask rule that would match if a field cgh could not
    determine had a matching value: every constraint on a known field matches
    and at least one constraint is on an undetermined field."""
    if rule.verdict == "allow" or not action.unknown:
        return False
    if not fnmatchcase(action.verb, rule.verb):
        return False
    on_unknown = False
    for key, alts in rule.constraints:
        if key in action.unknown and key not in action.fields:
            on_unknown = True
            continue
        value = action.fields.get(key)
        if value is None or not any(fnmatchcase(value, a) for a in alts):
            return False
    return on_unknown


# ---------------------------------------------------------------------------
# Reading the current branch
# ---------------------------------------------------------------------------


def current_branch(
    repo_dir: str | Path, timeout: float = GIT_BRANCH_TIMEOUT_S
) -> str | None:
    """The checked-out branch of ``repo_dir``, or None when it cannot be read
    in time (no repository, no git, a slow git, a detached HEAD)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = proc.stdout.strip()
    if proc.returncode != 0 or not name or name == "HEAD":
        return None
    return name


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


# An assignment that points git at a repository whatever the directory is.
_GIT_ENV_RE = re.compile(r"^(?:GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR)=")
# What a path must not hold for cgh to follow it: the shell would expand it.
_UNREADABLE_PATH_RE = re.compile(r"[$`*?\[\]{}]")


def _resolved(path: str | Path) -> Path | None:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


class _Context:
    """What a recogniser may read: the branch of a directory, read once, and
    the directory the command line has moved to (``cd``, ``pushd``)."""

    def __init__(
        self,
        cwd: str | Path | None,
        branch_reader: Callable[[Path], str | None] | None,
        which: Callable[[str], str | None] | None,
        git_timeout: float | None = None,
        scope: str | Path | None = None,
        repo_dir: Callable[[str], str | Path | None] | None = None,
    ) -> None:
        self.cwd = Path(cwd) if cwd else None
        budget = GIT_BRANCH_TIMEOUT_S if git_timeout is None else git_timeout
        self._reader = branch_reader or (
            lambda path: current_branch(path, timeout=budget)
        )
        self.which = which or shutil.which
        self._cache: dict[str, str | None] = {}
        self.scope = _resolved(scope) if scope else None
        self.repo_dir = repo_dir
        # Where the command line stands: None is the caller's directory, a
        # path is where a `cd` went, and `lost` says a `cd` could not be read.
        # `sure` holds while that directory is certain: a literal `cd` that
        # every later segment follows through `&&`. After `;`, `||`, a pipe
        # or a parenthesis the `cd` may not have run, or not for this
        # segment, and `here` is only the best guess.
        self.here: Path | None = None
        self.lost = False
        self.sure = True
        # Whether the segment being read certainly runs once its line is
        # reached: it opens the line, a `;` list or a subshell, or follows
        # through `&&`. A `cd` behind `||` or a pipe may never run.
        self.reached = True
        # GIT_DIR or GIT_WORK_TREE appears somewhere on the line: the
        # directory may no longer say which repository a git command acts
        # on. Like any doubt, this only keeps the caller's branch in play;
        # it never takes an action away from the repository it points at.
        self.git_env = False

    def moved(self) -> bool:
        return self.lost or self.here is not None

    def after(self, operator: str) -> None:
        """Note the operator that leads to the next segment."""
        if operator in ("", "&&"):
            return
        if self.moved():
            self.sure = False
        # `;` and `(` start a list of their own; behind anything else
        # (`||`, a pipe, `&`, a closing parenthesis) nothing is certain.
        self.reached = operator in (";", "(")

    def _dir(self, subdir: str | None = None) -> Path | None:
        """The directory a command of this segment runs in, resolved; None
        when it cannot be told."""
        if self.lost:
            return None
        base = self.here if self.here is not None else self.cwd
        if subdir:
            if _UNREADABLE_PATH_RE.search(subdir):
                return None
            named = Path(subdir).expanduser()
            if named.is_absolute():
                base = named
            elif base is not None:
                base = base / named
            else:
                return None
        return _resolved(base) if base is not None else None

    def place(self, subdir: str | None = None) -> str:
        """``_dir`` as the string an Action carries, "" when unknown."""
        target = self._dir(subdir)
        return str(target) if target is not None else ""

    def chdir(self, args: Sequence[str]) -> None:
        """Follow ``cd <dir>`` or ``pushd <dir>``. A target the shell would
        expand, ``cd -`` and ``popd`` lose the directory: nothing is guessed."""
        names = [a for a in args if not a.startswith("-") or a == "-"]
        if not names:
            names = ["~"]
        target = None if names[0] == "-" else self._dir(names[0])
        if target is None:
            self.lost, self.sure = True, False
            return
        # An absolute target does not depend on where the line stood before,
        # but the `cd` itself must be certain to run.
        absolute = Path(names[0]).expanduser().is_absolute()
        self.sure = self.reached and (absolute or self.sure)
        self.here, self.lost = target, False

    def unfollow(self) -> None:
        """The line moved somewhere cgh does not follow."""
        self.lost, self.sure = True, False

    def branch(self, subdir: str | None = None) -> str | None:
        if self.cwd is None:
            return None
        # The directory comes from the string being checked: git is only run
        # inside the caller's checkout, or inside the repository whose rules
        # are being applied (`scope`), never in another directory the action
        # names. Elsewhere the field is absent.
        root = _resolved(self.cwd)
        if root is None:
            return None
        named = self._dir(subdir)
        if self.scope is not None:
            target = named
            if target is None or not _inside(target, self.scope):
                return None
        elif named is not None and self.sure and not self.git_env:
            target = named
            if not _inside(target, root):
                return None
        else:
            # The line may have moved, or not: the caller's directory is
            # still what a rule on the branch is checked against.
            target = _resolved(self.cwd / subdir) if subdir else root
            if target is None or not _inside(target, root):
                return None
        key = str(target)
        if key not in self._cache:
            try:
                self._cache[key] = self._reader(target)
            except Exception:
                self._cache[key] = None
        return self._cache[key]


# ---------------------------------------------------------------------------
# Splitting a command line
# ---------------------------------------------------------------------------

_PUNCT = frozenset("();<>|&")
# Only these are redirections, whose target is a file and not a command. Any
# other run of shell punctuation (`|`, `&&`, `(`, `<(`, `;`...) starts a new
# segment, so a command inside a subshell or a process substitution is read.
_REDIRECT_RE = re.compile(r"^(?:[<>]{1,3}|[<>]&|&>{1,2}|>\|)$")
_WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "command",
        "exec",
        "time",
        "nohup",
        "xargs",
        "nice",
        "ionice",
        "stdbuf",
        "timeout",
    }
)
# A token holding a command substitution: bash runs what is inside.
_SUBST_RE = re.compile(r"\$\((.*)\)|`([^`]*)`", re.DOTALL)
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# The options of each wrapper that take a value. One table per wrapper: an
# option that takes a value for one (`xargs -n 1`) is a plain flag for another
# (`sudo -n`), and skipping a word too many would hide the command.
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U"}),
    "env": frozenset({"-u", "-C", "-P"}),
    "xargs": frozenset({"-n", "-I", "-P", "-L", "-d", "-E", "-s", "-a"}),
    "time": frozenset({"-o", "-f"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n"}),
    "ionice": frozenset({"-c", "-n", "-p"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "timeout": frozenset({"-s", "-k"}),
}
# Wrappers whose first argument is not the command: `timeout 5 <cmd>`.
_WRAPPER_LEADING_ARGS: dict[str, int] = {"timeout": 1}


def _newlines_to_separators(text: str) -> str:
    """Turn each unquoted newline into ``;`` so a quoted one stays in its token.
    A newline right after an unescaped backslash continues the line, as in a
    shell: both are dropped. After an escaped backslash it is a separator."""
    out: list[str] = []
    quote = ""
    escaped = False
    for ch in text.replace("\r\n", "\n"):
        if escaped:
            escaped = False
            if ch == "\n":
                out.pop()  # the backslash: a line continuation
            else:
                out.append(ch)
            continue
        if ch == "\\" and quote != "'":
            out.append(ch)
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = ""
            out.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        out.append(" ; " if ch in "\r\n" else ch)
    return "".join(out)


def split_segments(command: str) -> list[list[str]] | None:
    """Split a command line into argv segments on ``&&``, ``||``, ``;``, ``|``
    and newlines. None when the string cannot be tokenised (an open quote)."""
    linked = _linked_segments(command)
    return None if linked is None else [argv for _operator, argv in linked]


def _linked_segments(command: str) -> list[tuple[str, list[str]]] | None:
    """``split_segments`` with, for each segment, the shell punctuation that
    leads to it: "" for the first, then ``&&``, ``;``, ``|``, ``(``... When
    several runs of punctuation precede a segment, any that is not ``&&``
    wins, since only ``&&`` says the previous command succeeded."""
    lexer = shlex.shlex(
        _newlines_to_separators(command), posix=True, punctuation_chars=True
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[tuple[str, list[str]]] = []
    current: list[str] = []
    operator = ""
    pending = ""
    skip_next = False
    for tok in tokens:
        is_punct = bool(tok) and all(c in _PUNCT for c in tok)
        if skip_next:
            skip_next = False
            if not is_punct:
                # The target file of a redirection. A target that is itself
                # punctuation (`> >(cmd)`) is not skipped: it starts a segment.
                continue
        if is_punct:
            if _REDIRECT_RE.match(tok):
                # A redirection: drop the operator and its target file.
                skip_next = True
                continue
            if current:
                segments.append((operator, current))
                pending = ""
            current = []
            if pending in ("", "&&"):
                pending = tok
            continue
        if not current:
            operator = pending
        current.append(tok)
    if current:
        segments.append((operator, current))
    return segments


def _strip_wrappers(argv: list[str]) -> tuple[list[str], list[str]]:
    """Drop leading ``VAR=value`` assignments and wrapper commands. Returns
    ``(argv, scripts)``: ``scripts`` are command strings a wrapper carries
    itself (``env -S "<command>"``), to be read like a shell script."""
    scripts: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if _ASSIGN_RE.match(tok):
            i += 1
            continue
        if tok in _WRAPPERS:
            value_flags = _WRAPPER_VALUE_FLAGS.get(tok, frozenset())
            is_env = tok == "env"
            i += 1
            while i < len(argv) and (
                argv[i].startswith("-") or _ASSIGN_RE.match(argv[i])
            ):
                flag = argv[i]
                i += 1
                if is_env and flag in ("-S", "--split-string") and i < len(argv):
                    scripts.append(argv[i])
                    i += 1
                elif is_env and flag.startswith("--split-string="):
                    scripts.append(flag.partition("=")[2])
                elif flag in value_flags and i < len(argv):
                    i += 1
            i += _WRAPPER_LEADING_ARGS.get(tok, 0)
            continue
        break
    return argv[i:], scripts


def _script_of(argv: list[str]) -> str | None:
    """The script of ``bash -c '<script>'``, with ``-c`` alone or inside a
    cluster of short flags (``-lc``)."""
    for idx, tok in enumerate(argv[1:], start=1):
        if tok == "--":
            return None
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            return argv[idx + 1] if idx + 1 < len(argv) else None
    return None


def _parse_args(
    args: Sequence[str], value_flags: Iterable[str] = ()
) -> tuple[dict[str, str], list[str]]:
    """Split arguments into ``(flags, positionals)``.

    ``--key=value`` and a flag listed in ``value_flags`` carry their value;
    every other flag maps to ``""``. Everything after ``--`` is positional.
    """
    takes_value = frozenset(value_flags)
    flags: dict[str, str] = {}
    positionals: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            positionals.extend(args[i + 1 :])
            break
        if tok.startswith("--") and "=" in tok:
            key, _, value = tok.partition("=")
            flags[key] = value
        elif tok.startswith("-") and len(tok) > 1:
            if tok in takes_value and i + 1 < len(args):
                flags[tok] = args[i + 1]
                i += 1
            else:
                flags[tok] = ""
        else:
            positionals.append(tok)
        i += 1
    return flags, positionals


def _has(flags: dict[str, str], *names: str) -> bool:
    return any(n in flags for n in names)


def _short(flags: dict[str, str], letter: str) -> bool:
    """A short flag, alone or inside a cluster (``-fu``)."""
    return any(
        f.startswith("-") and not f.startswith("--") and letter in f[1:] for f in flags
    )


# ---------------------------------------------------------------------------
# Recognisers: one per command, each returns the actions it claims or None
# ---------------------------------------------------------------------------

_GIT_GLOBAL_VALUE_FLAGS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)
_PUSH_VALUE_FLAGS = ("-o", "--push-option", "--repo", "--receive-pack", "--exec")
_TAG_VALUE_FLAGS = (
    "-m",
    "-F",
    "-u",
    "--message",
    "--file",
    "--local-user",
    "--cleanup",
)
_COMMIT_VALUE_FLAGS = (
    "-m",
    "-F",
    "-C",
    "-c",
    "--message",
    "--file",
    "--author",
    "--date",
    "--fixup",
    "--squash",
    "--reuse-message",
    "--reedit-message",
    "--cleanup",
    "--template",
    "--trailer",
)


def _strip_ref(ref: str) -> str:
    return ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref


def _git_globals(argv: list[str]) -> tuple[int, str | None, bool]:
    """The global options that sit between ``git`` and the subcommand:
    ``(index of the subcommand, the -C directory, another repository named
    by --git-dir or --work-tree)``."""
    i = 1
    subdir: str | None = None
    foreign_repo = False
    while i < len(argv) and argv[i].startswith("-"):
        tok = argv[i]
        if tok in _GIT_GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
            if tok == "-C":
                subdir = argv[i + 1]
            elif tok in ("--git-dir", "--work-tree"):
                foreign_repo = True
            i += 2
            continue
        if tok.startswith(("--git-dir=", "--work-tree=")):
            foreign_repo = True
        i += 1
    return i, subdir, foreign_repo


def _git(argv: list[str], raw: str, ctx: _Context) -> list[Action] | None:
    i, subdir, foreign_repo = _git_globals(argv)
    if i >= len(argv):
        return None
    sub, args = argv[i], argv[i + 1 :]

    def branch_now() -> str | None:
        # Another repository named by --git-dir: its branch is not read.
        return None if foreign_repo else ctx.branch(subdir)

    if sub == "push":
        return _git_push(args, raw, branch_now)
    if sub == "commit":
        flags, _pos = _parse_args(args, _COMMIT_VALUE_FLAGS)
        fields: dict[str, str] = {}
        on_branch = branch_now()
        if on_branch:
            fields["on_branch"] = on_branch
        if _has(flags, "--amend"):
            fields["amend"] = "yes"
        unknown = () if on_branch else ("on_branch",)
        return [Action("git.commit", fields, raw, unknown)]
    if sub == "tag":
        flags, pos = _parse_args(args, _TAG_VALUE_FLAGS)
        if _has(flags, "--list", "--delete", "--verify") or any(
            _short(flags, c) for c in "ldv"
        ):
            return None
        if not pos:
            return None
        return [Action("git.tag", {"tag": pos[0]}, raw)]
    if sub == "branch":
        flags, pos = _parse_args(args)
        if _has(flags, "--delete") or _short(flags, "d") or _short(flags, "D"):
            return [
                Action("git.branch.delete", {"branch": _strip_ref(b)}, raw) for b in pos
            ] or [Action("git.branch.delete", {}, raw)]
        return None
    if sub == "reset":
        flags, _pos = _parse_args(args)
        if _has(flags, "--hard"):
            return [Action("git.reset", {"hard": "yes"}, raw)]
        return None
    if sub in ("rebase", "merge", "checkout", "stash"):
        value_flags = (
            ("-b", "-B", "--orphan")
            if sub == "checkout"
            else ("--onto", "-s", "--strategy", "-X", "-m")
        )
        flags, pos = _parse_args(args, value_flags)
        fields = {}
        named = ""
        if sub == "checkout":
            named = flags.get("-b") or flags.get("-B") or flags.get("--orphan") or ""
            if not named and pos and "--" not in args[: args.index(pos[0]) + 1]:
                named = pos[0]
        elif sub != "stash" and pos:
            named = pos[0]
        if named:
            fields["branch"] = _strip_ref(named)
        return [Action(f"git.{sub}", fields, raw)]
    return None


def _git_push(
    args: list[str], raw: str, branch_now: Callable[[], str | None]
) -> list[Action]:
    flags, pos = _parse_args(args, _PUSH_VALUE_FLAGS)
    base: dict[str, str] = {}
    force = _has(
        flags, "--force", "--force-with-lease", "--force-if-includes"
    ) or _short(flags, "f")
    delete = _has(flags, "--delete") or _short(flags, "d")
    if _has(flags, "--tags"):
        base["tags"] = "yes"
    if _has(flags, "--all", "--mirror", "--branches"):
        base["all"] = "yes"
    if pos:
        base["remote"] = pos[0]
    refspecs = pos[1:]

    # Each target is (fields, branch_unknown): the destination of one refspec.
    targets: list[tuple[dict[str, str], bool]] = []
    is_tag_name = False
    for spec in refspecs:
        one: dict[str, str] = {}
        if is_tag_name:
            # `git push <remote> tag <name>`
            is_tag_name = False
            targets.append(({"tag": spec}, False))
            continue
        if spec == "tag":
            is_tag_name = True
            continue
        if spec.startswith("+"):
            one["force"] = "yes"
            spec = spec[1:]
        src, sep, dst = spec.partition(":")
        if sep and not src and dst:
            one["delete"] = "yes"
        name = dst if sep else src
        if name in ("HEAD", "@"):
            name = branch_now() or ""
        if name.startswith("refs/tags/"):
            one["tag"] = name[len("refs/tags/") :]
        elif name:
            one["branch"] = _strip_ref(name)
        # No name: HEAD that could not be read, or the matching refspec `:`.
        targets.append((one, not name))
    if not targets:
        one = {}
        implicit = None if "all" in base else branch_now()
        if implicit:
            one["branch"] = implicit
        # --all, --mirror and --branches push branches cgh does not list.
        targets.append((one, not implicit))

    actions: list[Action] = []
    for one, branch_unknown in targets:
        fields = {**base, **one}
        if force:
            fields["force"] = "yes"
        if delete:
            fields["delete"] = "yes"
        unknown = ("branch",) if branch_unknown else ()
        actions.append(Action("git.push", fields, raw, unknown))
        if fields.get("delete") == "yes" and "branch" in fields:
            actions.append(
                Action("git.branch.delete", {"branch": fields["branch"]}, raw)
            )
    return actions


_GH_VALUE_FLAGS = (
    "-R",
    "--repo",
    "-b",
    "--body",
    "-t",
    "--title",
    "--subject",
    "-F",
    "--body-file",
    "--notes-file",
    "-n",
    "--notes",
    "-B",
    "--base",
    "-H",
    "--head",
    "-a",
    "--assignee",
    "-l",
    "--label",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "-r",
    "--reviewer",
    "-T",
    "--template",
    "-c",
    "--comment",
    "--target",
    "--match-head-commit",
    "-A",
    "--author-email",
    "--discussion-category",
    "--notes-start-tag",
)


def _gh(argv: list[str], raw: str, ctx: _Context) -> list[Action] | None:
    if len(argv) < 3:
        return None
    group, verb, args = argv[1], argv[2], argv[3:]
    flags, pos = _parse_args(args, _GH_VALUE_FLAGS)
    fields: dict[str, str] = {}
    if group == "pr" and verb == "merge":
        if pos:
            fields["pr"] = pos[0]
        for name, method in (
            ("--merge", "merge"),
            ("-m", "merge"),
            ("--squash", "squash"),
            ("-s", "squash"),
            ("--rebase", "rebase"),
            ("-r", "rebase"),
        ):
            if name in flags:
                fields["method"] = method
                break
        if _has(flags, "--admin"):
            fields["admin"] = "yes"
        return [Action("gh.pr.merge", fields, raw)]
    if group == "pr" and verb in ("create", "close"):
        base = flags.get("--base") or flags.get("-B")
        if base:
            fields["base"] = base
        if verb == "close" and pos:
            fields["pr"] = pos[0]
        return [Action(f"gh.pr.{verb}", fields, raw)]
    if group == "release" and verb == "create":
        if pos:
            fields["tag"] = pos[0]
        return [Action("gh.release.create", fields, raw)]
    if group == "issue" and verb == "create":
        repo = flags.get("--repo") or flags.get("-R")
        if repo:
            fields["repo"] = repo
        return [Action("gh.issue.create", fields, raw)]
    return None


def _publish(argv: list[str], raw: str, ctx: _Context) -> list[Action] | None:
    tool = argv[0]
    wanted = "upload" if tool == "twine" else "publish"
    if len(argv) > 1 and argv[1] == wanted:
        return [Action("pkg.publish", {"tool": tool}, raw)]
    return None


def _tool_install(argv: list[str], raw: str, ctx: _Context) -> list[Action] | None:
    tool = argv[0]
    rest = argv[2:] if tool == "uv" and argv[1:2] == ["tool"] else argv[1:]
    if tool == "uv" and argv[1:2] != ["tool"]:
        return _publish(argv, raw, ctx)
    if not rest or rest[0] not in ("install", "upgrade"):
        return None
    _flags, pos = _parse_args(
        rest[1:], ("--python", "-p", "--with", "--index", "--from")
    )
    fields = {"tool": tool}
    if pos:
        fields["package"] = pos[0]
    return [Action("tool.install", fields, raw)]


def _kill(argv: list[str], raw: str, ctx: _Context) -> list[Action] | None:
    _flags, pos = _parse_args(argv[1:])
    fields = {"pattern": pos[-1]} if pos else {}
    return [Action("proc.kill", fields, raw)]


# The recogniser table: argv[0] -> recogniser. Adding a verb is one entry here
# (or one branch in the recogniser of its command) and one test.
RECOGNISERS: dict[str, Callable[[list[str], str, _Context], list[Action] | None]] = {
    "git": _git,
    "gh": _gh,
    "uv": _tool_install,
    "pipx": _tool_install,
    "twine": _publish,
    "npm": _publish,
    "pnpm": _publish,
    "cargo": _publish,
    "pkill": _kill,
    "killall": _kill,
}

# The alias list: a short phrase, French or English. Each pattern matches the
# whole input, so a phrase followed by a shell separator is never read as the
# phrase alone and its other segments are checked too. Each entry is
# (pattern, verb, field names).
_NUM = r"#?(?P<pr>\d+)"
ALIASES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), verb, fields)
    for pattern, verb, fields in (
        (
            rf"^(?:merge|fusionner|fusionne)\s+(?:the\s+|la\s+)?(?:pr|pull\s+request)\s*{_NUM}\s*$",
            "gh.pr.merge",
            ("pr",),
        ),
        (
            r"^(?:tag|tagger|tague|taguer)\s+(?P<tag>v?\d[\w.+\-]*)\s*$",
            "git.tag",
            ("tag",),
        ),
        (
            r"^(?:push|pousser|pousse)\s+(?:to|on|sur|vers)\s+(?P<branch>[\w./\-]+)\s*$",
            "git.push",
            ("branch",),
        ),
        (
            r"^(?:release|publier|publie|sortir)\s+(?:la\s+)?(?P<tag>v?\d[\w.+\-]*)\s*$",
            "gh.release.create",
            ("tag",),
        ),
        (
            r"^(?:commit|commiter|committer)\s+(?:to|on|sur)\s+(?P<on_branch>[\w./\-]+)\s*$",
            "git.commit",
            ("on_branch",),
        ),
    )
)

# Commands a shell knows without a file on PATH.
_BUILTINS = frozenset(
    {"cd", "export", "source", "eval", "echo", "unset", "alias", "set", "kill", "."}
)
_COMMANDISH = frozenset("/=.*~$-_")


def _looks_like_command(argv: list[str], ctx: _Context) -> bool:
    """Tell a command from a phrase nobody recognised: an argument that looks
    like a flag, a path or an assignment, a shell builtin, or a first word
    that is a program on PATH."""
    head = argv[0]
    if head in _BUILTINS or any(c in _COMMANDISH for c in head):
        return True
    if any(c in _COMMANDISH for tok in argv[1:] for c in tok):
        return True
    try:
        return bool(ctx.which(head))
    except Exception:
        return False


def _match_alias(text: str) -> Action | None:
    for pattern, verb, names in ALIASES:
        m = pattern.match(text)
        if m:
            return Action(verb, {n: m.group(n) for n in names}, text)
    return None


def normalise(
    action: str,
    cwd: str | Path | None = None,
    branch_reader: Callable[[Path], str | None] | None = None,
    which: Callable[[str], str | None] | None = None,
    git_timeout: float | None = None,
    scope: str | Path | None = None,
    repo_dir: Callable[[str], str | Path | None] | None = None,
) -> list[Action]:
    """Turn an action string into its actions. An empty list means the input
    was not recognised: an empty or unparseable string, or a phrase no alias
    matches. ``git_timeout`` is the budget, in seconds, of the one git call
    that reads the branch (GIT_BRANCH_TIMEOUT_S by default): a caller with a
    tighter deadline passes its own.

    Each action carries ``where``, the directory it runs in. ``scope`` is the
    repository whose rules the caller applies when it is not the one of
    ``cwd``: the branch is then read there, for the actions that run there.
    ``repo_dir`` turns the ``owner/name`` of ``gh --repo`` into a checkout
    the caller knows, or None."""
    ctx = _Context(cwd, branch_reader, which, git_timeout, scope, repo_dir)
    return _normalise(action or "", ctx, 0)


def _nested(script: str, ctx: _Context, depth: int) -> list[Action]:
    """The actions of a script the command runs from inside (a command
    substitution, ``bash -c``, ``env -S``). One that cannot be read still
    counts as an action. A ``cd`` inside it does not move the line outside."""
    saved = (ctx.here, ctx.lost, ctx.sure)
    try:
        inner = _normalise(script, ctx, depth + 1) if depth < _MAX_SCRIPT_DEPTH else []
    finally:
        ctx.here, ctx.lost, ctx.sure = saved
    return inner or [
        Action("shell.script", {"args": script}, script, where=ctx.place())
    ]


def _gh_repo(argv: list[str]) -> str:
    """The repository ``gh --repo`` (or ``-R``) names, "" when it names none."""
    flags, _pos = _parse_args(argv[1:], _GH_VALUE_FLAGS)
    return flags.get("--repo") or flags.get("-R") or ""


def _place(head: str, argv: list[str], ctx: _Context) -> str:
    """Where the command ``head`` of this segment runs: where the line
    stands, unless the command names its own directory."""
    if head == "git":
        _i, subdir, foreign_repo = _git_globals(argv)
        # A repository named by --git-dir is not one cgh places.
        return "" if foreign_repo else ctx.place(subdir)
    if head == "gh" and ctx.repo_dir is not None:
        slug = _gh_repo(argv)
        try:
            known = ctx.repo_dir(slug) if slug else None
        except Exception:
            known = None
        if known:
            return str(known)
    return ctx.place()


def _normalise(text: str, ctx: _Context, depth: int) -> list[Action]:
    text = text.strip()
    if not text:
        return []
    alias = _match_alias(text)
    if alias is not None:
        return [alias]
    segments = _linked_segments(text)
    if not segments:
        return []
    compound = len(segments) > 1
    out: list[Action] = []
    # Command substitutions are looked for in the whole text, not per segment:
    # one can sit in an argument, between double quotes, or in the target of a
    # redirection, which the segments no longer hold.
    for m in _SUBST_RE.finditer(text):
        script = m.group(1) if m.group(1) is not None else m.group(2)
        out.extend(_nested(script, ctx, depth))
    for operator, seg in segments:
        ctx.after(operator)
        if any(_GIT_ENV_RE.match(tok) for tok in seg):
            # Set for one command or exported for the rest of the line. Any
            # word of that shape counts: telling an assignment from an
            # argument is not worth a wrong guess here.
            ctx.git_env = True
        start = len(out)
        head, argv = _segment(seg, ctx, depth, compound, out)
        # Where this segment's own actions run; a nested script placed its.
        place = _place(head, argv, ctx)
        for act in out[start:]:
            if act.where is None:
                act.where = place
        _move(seg, ctx)
    return out


def _move(seg: list[str], ctx: _Context) -> None:
    """Follow a segment that moves the shell. Only the builtin itself does:
    `cd` as the first word, after assignments at most. Behind a wrapper
    (`env cd`, `sudo cd`) or as a path (`/usr/bin/cd`) another process
    changes directory and the line stays where it was. `command cd`,
    `builtin cd` and `time cd` do move the shell: they are followed as a
    guess, so the repository they point at judges what comes next while the
    caller's branch stays in play."""
    words = list(seg)
    while words and _ASSIGN_RE.match(words[0]):
        words.pop(0)
    prefixed = False
    while words and words[0] in ("command", "builtin", "time"):
        prefixed = True
        words.pop(0)
        while words and words[0].startswith("-"):
            words.pop(0)
    if not words:
        return
    builtin, args = words[0], words[1:]
    if prefixed:
        if builtin in ("cd", "pushd") and not any(
            a.startswith(("-", "+")) for a in args
        ):
            ctx.chdir(args)
            ctx.sure = False
        elif builtin in ("cd", "pushd", "popd"):
            ctx.unfollow()
        return
    if builtin == "cd":
        ctx.chdir(args)
    elif builtin == "pushd":
        # `pushd -n` does not move, `pushd +1` rotates a stack cgh does not
        # keep: only a plain directory is followed.
        if any(a.startswith(("-", "+")) for a in args):
            ctx.unfollow()
        else:
            ctx.chdir(args)
    elif builtin == "popd":
        ctx.unfollow()


def _segment(
    seg: list[str], ctx: _Context, depth: int, compound: bool, out: list[Action]
) -> tuple[str, list[str]]:
    """Append the actions of one segment to ``out``. Returns the command of
    the segment and its argv, once wrappers and assignments are dropped."""
    # What bash would run from inside this segment: a command substitution
    # in an argument, a command string handed to a wrapper. Each is read
    # as a script; one that cannot be read still counts as an action, so
    # it is never dropped and the command cannot come out allowed on the
    # strength of its other parts.
    argv, scripts = _strip_wrappers(seg)
    for script in scripts:
        out.extend(_nested(script, ctx, depth))
    if not argv:
        if not scripts:
            # Only wrappers and assignments: still something that runs.
            out.append(
                Action(f"shell.{seg[0]}", {"args": " ".join(seg[1:])}, shlex.join(seg))
            )
        return "", argv
    if len(argv) < len(seg) and any(t in _WRAPPERS for t in seg):
        # Behind a wrapper, where the command starts is a guess: an option
        # this table does not know may take a value. So every later word
        # that names a recognised command is read as one too.
        for j in range(1, len(argv)):
            name = Path(argv[j]).name if "/" in argv[j] else argv[j]
            late = RECOGNISERS.get(name)
            if late is None:
                continue
            late_argv = [name, *argv[j + 1 :]]
            found = late(late_argv, shlex.join(argv[j:]), ctx) or []
            for act in found:
                act.where = _place(name, late_argv, ctx)
            out.extend(a for a in found if a not in out)
    raw = shlex.join(argv)
    head = Path(argv[0]).name if "/" in argv[0] else argv[0]
    if head in _SHELLS or head == "eval":
        script = " ".join(argv[1:]) if head == "eval" else _script_of(argv)
        if script is not None:
            out.extend(_nested(script, ctx, depth))
            return head, argv
    recogniser = RECOGNISERS.get(head)
    claimed = recogniser([head, *argv[1:]], raw, ctx) if recogniser else None
    if claimed:
        out.extend(claimed)
        return head, argv
    if compound or depth or out or recogniser or _looks_like_command(argv, ctx):
        out.append(
            Action(f"shell.{head}", {"args": " ".join(argv[1:])}, raw),
        )
    return head, argv


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def _recorded(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _next_line(verdict: str, recognised: bool, rule: dict[str, Any] | None) -> str:
    if verdict == "allowed":
        return "Proceed: an allow rule of the user covers this action."
    if verdict == "refused" and rule:
        return (
            f"Do not proceed: standing instruction #{rule['entry_id']} "
            f"({rule['title']}) refuses this action."
        )
    if verdict == "ask" and rule:
        return (
            f"Ask the user before proceeding: standing instruction "
            f"#{rule['entry_id']} ({rule['title']}) says to ask."
        )
    if not recognised:
        return (
            "cgh could not read this as an action, so no rule was tried: use "
            "your own judgment and ask before anything hard to undo."
        )
    return (
        "No rule covers this: use your own judgment and ask before anything "
        "hard to undo."
    )


def evaluate(
    action: str,
    entries: Sequence[dict[str, Any]],
    session_id: str = "",
    cwd: str | Path | None = None,
    branch_reader: Callable[[Path], str | None] | None = None,
    which: Callable[[str], str | None] | None = None,
    git_timeout: float | None = None,
    scope: str | Path | None = None,
    repo_dir: Callable[[str], str | Path | None] | None = None,
) -> dict[str, Any]:
    """Check ``action`` against the rule lines of ``entries``.

    ``entries`` are live standing instructions of the repo store, each with
    ``id``, ``title``, ``body``, ``session_id`` and ``ts``. An allow rule
    recorded by the calling session does not count for it: a session does not
    write its own permission slip.

    Without ``scope`` the entries are those of the caller's repository and
    every action is checked. With ``scope``, they are the rules of another
    repository the command aims at (``cd <scope> &&``, ``git -C <scope>``):
    only the actions that run inside it are checked, the others get
    ``no_rule``.
    """
    actions = normalise(action, cwd, branch_reader, which, git_timeout, scope, repo_dir)
    scope_root = _resolved(scope) if scope else None
    rules: list[tuple[dict[str, Any], Rule]] = []
    for entry in entries:
        parsed, _errors = parse_rule_lines(str(entry.get("body") or ""))
        rules.extend((entry, r) for r in parsed)

    ignored: list[dict[str, Any]] = []
    matched_all: list[tuple[int, dict[str, Any], Rule]] = []
    action_views: list[dict[str, Any]] = []
    for idx, act in enumerate(actions):
        matching: list[tuple[dict[str, Any], Rule]] = []
        # A scope that cannot be resolved aims at nothing: it never widens
        # into a check of every action.
        aimed = not scope or (
            scope_root is not None
            and bool(act.where)
            and _inside(Path(str(act.where)), scope_root)
        )
        for entry, rule in rules if aimed else ():
            if not rule_matches(rule, act):
                continue
            own = bool(session_id) and str(entry.get("session_id") or "") == session_id
            if rule.verdict == "allow" and own:
                item = {"entry_id": entry.get("id"), "line": rule.line}
                if item not in ignored:
                    ignored.append(item)
                continue
            matching.append((entry, rule))
        verdict = "no_rule"
        decider: tuple[dict[str, Any], Rule] | None = None
        for wanted in ("refuse", "ask", "allow"):
            decider = next((m for m in matching if m[1].verdict == wanted), None)
            if decider is not None:
                verdict = _ANSWER[wanted]
                break
        if verdict == "allowed" and any(
            _withheld_by_unknown(rule, act) for _entry, rule in rules
        ):
            # A refusal constrains a field cgh could not determine here: the
            # allow rule does not clear the action. It stays listed in others.
            verdict = "no_rule"
            decider = None
        matched_all.extend((idx, e, r) for e, r in matching)
        action_views.append(
            {
                "verb": act.verb,
                "fields": dict(act.fields),
                "raw": act.raw,
                "verdict": verdict,
                "rule_entry_id": decider[0].get("id") if decider else None,
                "_decider": decider,
            }
        )

    recognised = bool(action_views)
    overall = "no_rule"
    deciding: tuple[dict[str, Any], Rule] | None = None
    if action_views:
        strictest = min(action_views, key=lambda v: VERDICT_ORDER.index(v["verdict"]))
        overall = strictest["verdict"]
        deciding = strictest["_decider"]

    rule_view: dict[str, Any] | None = None
    if deciding is not None:
        entry, rule = deciding
        rule_view = {
            "entry_id": entry.get("id"),
            "title": str(entry.get("title") or ""),
            "line": rule.line,
            "reason": reason_of(str(entry.get("body") or "")),
            "recorded": _recorded(entry.get("ts")),
        }

    others: list[dict[str, Any]] = []
    for _idx, entry, rule in matched_all:
        if deciding is not None and entry is deciding[0] and rule is deciding[1]:
            continue
        item = {
            "entry_id": entry.get("id"),
            "line": rule.line,
            "verdict": _ANSWER[rule.verdict],
        }
        if item not in others:
            others.append(item)

    for view in action_views:
        del view["_decider"]

    out: dict[str, Any] = {
        "action": action,
        "verdict": overall,
        "recognised": recognised,
        "rule": rule_view,
        "actions": action_views,
        "others": others,
        "ignored": ignored,
    }
    if overall == "no_rule":
        out["related"] = []
    out["next"] = _next_line(overall, recognised, rule_view)
    return out


def search_words(actions: Sequence[dict[str, Any]]) -> str:
    """The words of the normalised actions (verb parts and field values), for
    the search that fills ``related``."""
    words: list[str] = []
    for act in actions:
        for part in str(act.get("verb") or "").split("."):
            if part and part != "shell" and part not in words:
                words.append(part)
        for key, value in (act.get("fields") or {}).items():
            if key == "args":
                continue
            for part in re.split(r"[^\w]+", str(value)):
                if len(part) > 2 and part != "yes" and part not in words:
                    words.append(part)
    return " ".join(words)
