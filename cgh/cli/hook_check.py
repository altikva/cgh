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
#              The check only ever restricts, and never by failing: a store
#              it cannot read, a slow git, an exception or a missed deadline
#              all give None, with one line on stderr saying it was skipped.

from __future__ import annotations

import os
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


def _verdict(command: str, root: Path, cwd: Path, session_id: str) -> dict | None:
    """The check_action answer for ``command``, or None when the store holds
    no rule line. Reads the store file itself: no owner is asked."""
    from cgh.state import action_rules
    from cgh.state.call_log import standing_rule_entries_readonly

    entries = standing_rule_entries_readonly(root, busy_timeout=STORE_BUSY_S)
    if not entries:
        return None
    return action_rules.evaluate(
        command, entries, session_id=session_id, cwd=cwd, git_timeout=GIT_TIMEOUT_S
    )


def _message(out: dict, advisory: bool) -> str:
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
    """
    started = time.monotonic()
    try:
        root = find_cgh_root(cwd)
        chosen = mode(root)
        if chosen == "off" or root is None:
            return None
        session_id = payload.get("session_id")
        session_id = session_id if isinstance(session_id, str) else ""

        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["out"] = _verdict(command, root, cwd, session_id)
            except sqlite3.Error as exc:
                box["why"] = f"the knowledge store could not be read ({exc})"
            except Exception as exc:
                box["why"] = f"{type(exc).__name__}: {exc}"

        # A daemon thread, so that a step that hangs past its own timeout
        # cannot hold the hook: past the budget the command simply runs.
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(max(0.0, BUDGET_S - (time.monotonic() - started)))
        if worker.is_alive():
            _note(f"no answer within {int(BUDGET_S * 1000)} ms")
            return None
        if "why" in box:
            _note(box["why"])
            return None
        out = box.get("out")
        if (
            not out
            or out.get("verdict") not in ("refused", "ask")
            or not out.get("rule")
        ):
            return None
        if chosen == "advise":
            return Decision("advise", _message(out, advisory=True))
        kind = "deny" if out["verdict"] == "refused" else "ask"
        return Decision(kind, _message(out, advisory=False))
    except Exception as exc:
        _note(f"{type(exc).__name__}: {exc}")
        return None
