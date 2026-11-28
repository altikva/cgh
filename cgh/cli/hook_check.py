# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Bash precheck asks check_action about the command before it
#              runs. A rule line of a standing instruction that refuses the
#              command, or says to ask, becomes a decision of the hook
#              (`check_action = "enforce"`) or a note to the model ("advise").
#              mode      which of enforce, advise, off applies
#              decision  the Decision for a command, or None
#              The rules are those of the repository the session runs in,
#              plus those of each other repository the command aims at
#              (`cd <dir> &&`, `git -C <dir>`, `gh --repo` of a declared
#              sibling), so a session started outside any repository is
#              still held by the rules of the checkout it acts on.
#              The check only ever restricts, and never by failing: a store
#              it cannot read, a slow git, an exception or a missed deadline
#              all give None, with one line on stderr saying it was skipped.

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cgh.core.config import find_cgh_root, global_config_path, state_dir_for

MODES = ("enforce", "advise", "off")
# Provisional default, pending the maintainer's choice between enforce and
# advise: the one that never blocks. Changing the default is this line.
DEFAULT_MODE = "advise"
ENV = "CGH_HOOK_CHECK_ACTION"

# Wall clock for the whole check, from the moment the hook has its payload.
BUDGET_S = 0.2
# Inside it: the wait on a locked store, and the one git call for the branch.
STORE_BUSY_S = 0.05
GIT_TIMEOUT_S = 0.1
# Other repositories a command aims at whose rules are read, at most. The
# budget above covers them all: one that is not reached in time is skipped.
MAX_AIMED_REPOS = 3

_RANK = {"deny": 0, "ask": 1, "advise": 2}
_REMOTE_URL_RE = re.compile(r"^\s*url\s*=\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Decision:
    """What the hook does about a command: ``deny`` or ``ask`` as a permission
    decision, ``advise`` as a note to the model. Never an allow."""

    kind: str
    message: str


def _note(why: str) -> None:
    """Say on stderr that the check did not run. Exit-0 stderr of a
    PreToolUse hook never reaches the model: this is for whoever debugs."""
    try:
        print(f"cgh check_action: skipped, {why}", file=sys.stderr)
    except OSError:
        pass  # a closed stderr must not turn a skipped check into a failure


def _configured(path: Path) -> Any:
    """``[hooks] check_action`` of one config file, or None when the file is
    missing, unreadable or does not carry the key."""
    import tomllib

    try:
        with open(path, "rb") as fh:
            hooks = tomllib.load(fh).get("hooks")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return hooks.get("check_action") if isinstance(hooks, dict) else None


def mode(root: Path | None) -> str:
    """The mode that applies: CGH_HOOK_CHECK_ACTION, else ``[hooks]
    check_action`` of the repo's config, else of the global one, else
    DEFAULT_MODE. A value that is not a mode turns the check off and says so
    on stderr: a typo must not silently enforce, nor silently disarm."""
    value: Any = os.environ.get(ENV, "").strip() or None
    source = ENV
    if value is None:
        paths = [state_dir_for(root) / "config.toml"] if root else []
        for path in (*paths, global_config_path()):
            value = _configured(path)
            if value is not None:
                source = f"[hooks] check_action in {path}"
                break
    if value is None:
        return DEFAULT_MODE
    if isinstance(value, str) and value.strip().lower() in MODES:
        return value.strip().lower()
    _note(f"{source} is {value!r}, expected one of: {', '.join(MODES)}")
    return "off"


def _verdict(
    command: str,
    root: Path,
    cwd: Path,
    session_id: str,
    scope: Path | None = None,
    git_timeout: float = GIT_TIMEOUT_S,
) -> dict | None:
    """The check_action answer for ``command`` under the rules of ``root``,
    or None when its store holds no rule line. Reads the store file itself:
    no owner is asked. ``scope`` is ``root`` when it is not the repository of
    ``cwd`` but one the command aims at."""
    from cgh.state import action_rules
    from cgh.state.call_log import standing_rule_entries_readonly

    entries = standing_rule_entries_readonly(root, busy_timeout=STORE_BUSY_S)
    if not entries:
        return None
    if scope is None:
        return action_rules.evaluate(
            command, entries, session_id=session_id, cwd=cwd, git_timeout=git_timeout
        )
    return action_rules.evaluate(
        command,
        entries,
        session_id=session_id,
        cwd=cwd,
        git_timeout=git_timeout,
        scope=scope,
        repo_dir=_checkout_of,
    )


def _slug(text: str) -> str:
    """``owner/name`` out of a ``gh --repo`` value or a remote URL, lower
    case, "" when the text does not end with two path parts."""
    text = text.strip().removesuffix("/").removesuffix(".git")
    parts = [p for p in re.split(r"[/:]", text) if p]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else ""


def _git_config_of(checkout: Path) -> Path | None:
    """The git config file of a checkout, a linked worktree included."""
    dot_git = checkout / ".git"
    if dot_git.is_dir():
        return dot_git / "config"
    try:
        pointer = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    git_dir = (checkout / pointer[len("gitdir:") :].strip()).resolve()
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir / "config"
    return (git_dir / common).resolve() / "config"


def _checkout_of(slug: str) -> Path | None:
    """The checkout ``gh --repo <slug>`` acts on, when it is one the user
    declared: an absolute ``[cgh] siblings`` entry of the global config, one
    of whose remotes is that repository. Files are read, git is not run, and
    the disk is not searched: an undeclared checkout is not found."""
    import tomllib

    wanted = _slug(slug)
    if not wanted:
        return None
    try:
        with open(global_config_path(), "rb") as fh:
            declared = (tomllib.load(fh).get("cgh") or {}).get("siblings")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None
    for raw in declared if isinstance(declared, list) else []:
        checkout = Path(str(raw)).expanduser()
        if not checkout.is_absolute():
            continue
        config = _git_config_of(checkout)
        try:
            text = (
                config.read_text(encoding="utf-8", errors="replace") if config else ""
            )
        except OSError:
            continue
        if any(_slug(url) == wanted for url in _REMOTE_URL_RE.findall(text)):
            return checkout.resolve()
    return None


def _aimed_roots(command: str, cwd: Path, cwd_root: Path | None) -> list[Path]:
    """The cgh repositories the command aims at, other than the one of the
    session's directory, in the order the command names them."""
    from cgh.state import action_rules

    # The branch is of no use to find where the actions run: git is not asked.
    actions = action_rules.normalise(
        command, cwd, branch_reader=lambda _path: None, repo_dir=_checkout_of
    )
    home_state = global_config_path().parent.resolve()
    roots: list[Path] = []
    for act in actions:
        if not act.where:
            continue
        root = find_cgh_root(act.where)
        if root is None or root == cwd_root or root in roots:
            continue
        if state_dir_for(root).resolve() == home_state:
            continue  # the user's global directory is not a repository
        roots.append(root)
    return roots[:MAX_AIMED_REPOS]


def _decide(
    command: str, cwd: Path, session_id: str, found: list[Decision], deadline: float
) -> None:
    """Append to ``found`` what each repository's rules say: the one of the
    session's directory first, exactly as if the command aimed nowhere else,
    then each repository the command aims at. A repository that cannot be
    read, or is not reached before ``deadline``, adds nothing and takes
    nothing away from the others."""

    def one(root: Path, scope: Path | None, git_timeout: float) -> None:
        try:
            _decide_one(command, root, cwd, session_id, scope, git_timeout, found)
        except Exception as exc:
            why = (
                f"the knowledge store could not be read ({exc})"
                if isinstance(exc, sqlite3.Error)
                else f"{type(exc).__name__}: {exc}"
            )
            _note(why if scope is None else f"{why}, rules of {root} not applied")

    cwd_root = find_cgh_root(cwd)
    if cwd_root is not None:
        one(cwd_root, None, GIT_TIMEOUT_S)
    try:
        aimed = _aimed_roots(command, cwd, cwd_root)
    except Exception as exc:
        _note(f"{type(exc).__name__}: {exc}, aimed repositories not looked for")
        return
    for root in aimed:
        left = deadline - time.monotonic()
        if left <= 0:
            _note(f"no time left for the rules of {root}")
            return
        one(root, root, min(GIT_TIMEOUT_S, left))


def _decide_one(
    command: str,
    root: Path,
    cwd: Path,
    session_id: str,
    scope: Path | None,
    git_timeout: float,
    found: list[Decision],
) -> None:
    chosen = mode(root)
    if chosen == "off":
        return
    if scope is None:
        out = _verdict(command, root, cwd, session_id)
    else:
        out = _verdict(command, root, cwd, session_id, scope, git_timeout)
    if not out or out.get("verdict") not in ("refused", "ask") or not out.get("rule"):
        return
    advisory = chosen == "advise"
    kind = "advise" if advisory else "deny" if out["verdict"] == "refused" else "ask"
    found.append(Decision(kind, _message(out, advisory, scope)))


def _message(out: dict, advisory: bool, repo: Path | None = None) -> str:
    rule = out["rule"]
    refused = out["verdict"] == "refused"
    segment = next(
        (
            a["raw"]
            for a in out.get("actions", [])
            if a.get("verdict") == out["verdict"]
            and a.get("rule_entry_id") == rule["entry_id"]
        ),
        "",
    )
    head = (
        f"[cgh check_action] Standing instruction #{rule['entry_id']} "
        f"({rule['title']}) "
        # Entry ids are per store: say whose, when it is not the session's.
        + (f"of {repo} " if repo is not None else "")
        + ("refuses this command" if refused else "says to ask the user first")
        + f": `{rule['line']}`"
    )
    if segment and segment != out.get("action"):
        head += f", matched by `{segment}`"
    parts = [head + "."]
    if rule.get("reason"):
        parts.append(rule["reason"])
    if advisory:
        parts.append(
            "The hook is advisory and does not block the command: "
            + ("do not run it." if refused else "ask the user before running it.")
        )
    elif refused:
        parts.append("Only the user changes that rule, by superseding the instruction.")
    return " ".join(parts)


def decision(payload: dict, command: str, cwd: Path) -> Decision | None:
    """What the hook must do about ``command``, or None for "nothing".

    A Decision is only ever the product of a rule line that was read, parsed
    and matched, inside BUDGET_S. Everything else is None: check turned off,
    no cgh root, no store, no rule line, verdict allowed or no_rule, and any
    failure (a locked or corrupt store, an exception in the normaliser or the
    matcher, a deadline missed), which leaves a line on stderr.

    When several repositories rule (the session's and the ones the command
    aims at), the strictest decision is returned: deny, then ask, then advise.
    BUDGET_S covers them all.
    """
    started = time.monotonic()
    try:
        # Off by the environment: nothing is read, not even the command.
        if os.environ.get(ENV, "").strip().lower() == "off":
            return None
        session_id = payload.get("session_id")
        session_id = session_id if isinstance(session_id, str) else ""

        found: list[Decision] = []
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                _decide(command, cwd, session_id, found, started + BUDGET_S)
            except sqlite3.Error as exc:
                box["why"] = f"the knowledge store could not be read ({exc})"
            except Exception as exc:
                box["why"] = f"{type(exc).__name__}: {exc}"

        # A daemon thread, so that a step that hangs past its own timeout
        # cannot hold the hook: past the budget the command simply runs,
        # under whatever the repositories read in time have said.
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(max(0.0, BUDGET_S - (time.monotonic() - started)))
        decided = list(found)
        if worker.is_alive():
            _note(f"no answer within {int(BUDGET_S * 1000)} ms")
        elif "why" in box:
            _note(box["why"])
        if not decided:
            return None
        return min(decided, key=lambda d: _RANK[d.kind])
    except Exception as exc:
        _note(f"{type(exc).__name__}: {exc}")
        return None
