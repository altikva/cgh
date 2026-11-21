# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh check "<action>"`: check an action against the rule lines
#              of this repo's standing instructions. Opens the knowledge store
#              directly and needs no running owner. The exit code carries the
#              verdict so a shell wrapper or a git hook can gate on it: 0 is
#              allowed and only allowed, 2 refused, 3 ask, 4 no rule, 1 error.

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from cgh.cli import console

EXIT_ALLOWED = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_ASK = 3
EXIT_NO_RULE = 4

_EXIT = {
    "allowed": EXIT_ALLOWED,
    "refused": EXIT_REFUSED,
    "ask": EXIT_ASK,
    "no_rule": EXIT_NO_RULE,
}
_STYLE = {"allowed": "green", "refused": "red", "ask": "yellow", "no_rule": "dim"}


def register_decision_parser(sub) -> None:
    p = sub.add_parser(
        "check",
        help="Check an action against this repo's standing instructions",
        description=(
            "Check an action (a shell command or a short phrase) against the "
            "cgh-rule lines of this repo's standing instructions. Exit code: "
            "0 allowed, 2 refused, 3 ask, 4 no rule covers it, 1 error."
        ),
    )
    p.add_argument(
        "action",
        help='The action: a shell command ("git push origin develop") or a phrase ("merge PR 12")',
    )
    p.add_argument("--root", default=os.getcwd(), help="Repo root (default: cwd)")
    p.add_argument(
        "--json", action="store_true", help="Print the check_action payload as JSON"
    )
    p.add_argument(
        "--session-id",
        default="",
        help="Calling session: an allow rule it recorded itself does not count",
    )


def cmd_check(args: argparse.Namespace) -> None:
    from cgh.core.config import state_dir_for
    from cgh.state.call_log import check_action

    root = Path(os.path.abspath(args.root))
    if not state_dir_for(root).is_dir():
        print(
            f"cgh check: no cgh store in {root} (run cgh init there first)",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ERROR)
    try:
        out = check_action(args.action, args.session_id or "", root)
    except (sqlite3.Error, OSError) as exc:
        print(f"cgh check: cannot read the knowledge store: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc

    verdict = out["verdict"]
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        raise SystemExit(_EXIT.get(verdict, EXIT_ERROR))

    style = _STYLE.get(verdict, "dim")
    console.print(f"[{style}]{verdict}[/{style}]  {args.action}", highlight=False)
    rule = out.get("rule")
    if rule:
        console.print(
            f"  {rule['line']}  [dim](standing instruction #{rule['entry_id']}: "
            f"{rule['title']})[/dim]",
            highlight=False,
        )
        if rule.get("reason"):
            for line in str(rule["reason"]).splitlines():
                console.print(f"  [dim]{line}[/dim]", highlight=False)
    for item in out.get("ignored") or []:
        console.print(
            f"  [dim]ignored (recorded by this session): {item['line']} "
            f"(#{item['entry_id']})[/dim]",
            highlight=False,
        )
    for hit in out.get("related") or []:
        console.print(
            f"  [dim]related: #{hit['id']} {hit['title']}[/dim]", highlight=False
        )
    console.print(f"  {out['next']}", highlight=False)
    raise SystemExit(_EXIT.get(verdict, EXIT_ERROR))
