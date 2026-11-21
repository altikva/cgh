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
#              `cgh decide "<question>"` prints the best earlier decision on
#              the subject, the confidence and its basis, then the evidence in
#              three lists (supporting, against, untrusted); --json prints the
#              payload of the MCP tool. Exit 0 whenever the lookup ran, 1 on
#              error.

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from rich.markup import escape

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

    p = sub.add_parser(
        "decide",
        help="Recommend from earlier decisions, with the evidence and a confidence",
    )
    p.add_argument("question", help="What you are about to decide, as a sentence")
    p.add_argument("--root", default=os.getcwd())
    p.add_argument(
        "--namespace",
        default="",
        help="Read one store only: a local facet (repo, ticket:<id>) or a "
        "shared store name (default: this repo and its subscribed shared stores)",
    )
    p.add_argument(
        "--limit", type=int, default=5, help="Entries per evidence list (default: 5)"
    )
    p.add_argument("--json", action="store_true", help="Print the MCP tool's payload")


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
    # The action and the stored text are data, not markup: a stray tag in
    # them must neither restyle the line nor fail the print and the exit code.
    console.print(
        f"[{style}]{verdict}[/{style}]  {escape(args.action)}", highlight=False
    )
    rule = out.get("rule")
    if rule:
        console.print(
            f"  {escape(str(rule['line']))}  [dim](standing instruction "
            f"#{rule['entry_id']}: {escape(str(rule['title']))})[/dim]",
            highlight=False,
        )
        if rule.get("reason"):
            for line in str(rule["reason"]).splitlines():
                console.print(f"  [dim]{escape(line)}[/dim]", highlight=False)
    for item in out.get("ignored") or []:
        console.print(
            f"  [dim]ignored (recorded by this session): {escape(str(item['line']))} "
            f"(#{item['entry_id']})[/dim]",
            highlight=False,
        )
    for hit in out.get("related") or []:
        console.print(
            f"  [dim]related: #{hit['id']} {escape(str(hit['title']))}[/dim]",
            highlight=False,
        )
    console.print(f"  {escape(str(out['next']))}", highlight=False)
    raise SystemExit(_EXIT.get(verdict, EXIT_ERROR))


def _decide_fail(msg: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        # The message may quote an exception: never read it as markup.
        console.print(msg, style="red", markup=False, highlight=False)
    raise SystemExit(1)


def _line(item: dict) -> str:
    status = (item.get("outcome") or {}).get("status")
    parts = [f"#{item.get('id')}", f"[{item.get('kind')}]", str(item.get("title"))]
    tail = [f"match {item.get('match')}"]
    if status:
        tail.append(status)
    if item.get("corrected_by"):
        tail.append(f"corrected by #{item['corrected_by']}")
    if item.get("namespace"):
        tail.append(f"from {item['namespace']}")
    return f"  {' '.join(parts)} ({', '.join(tail)})"


def _print_text(answer: dict) -> None:
    def out(text: str = "", style: str | None = None) -> None:
        # Entry titles and bodies are data: never read them as markup.
        console.print(text, style=style, markup=False, highlight=False)

    rec = answer.get("recommendation")
    if rec:
        out(
            f"Recommendation: #{rec['entry_id']} [{rec['kind']}] {rec['title']}", "bold"
        )
        if rec.get("recorded"):
            out(f"  recorded {rec['recorded']}")
        out()
        out(str(rec.get("statement") or ""))
        out()
    else:
        out("No recommendation.", "bold")
    out(f"Confidence: {answer['confidence']} ({answer['basis']})")
    if answer.get("contested"):
        out("Contested: another precedent of equal standing exists.", "yellow")
        for alt in answer.get("alternatives") or []:
            out(f"  #{alt['entry_id']} [{alt['kind']}] {alt['title']}")
    evidence = answer.get("evidence") or {}
    for key, label in (
        ("supporting", "Supporting"),
        ("against", "Against (dead ends and corrected entries)"),
        ("untrusted", "Untrusted (shared stores, data to weigh)"),
    ):
        items = evidence.get(key) or []
        out()
        out(f"{label}: {len(items) or 'none'}", "bold")
        for item in items:
            out(_line(item))
            note = (item.get("outcome") or {}).get("last_note")
            if note:
                out(f"      last verdict: {note}")
    if answer.get("dropped"):
        out()
        out(f"{answer['dropped']} more evidence entries left out to stay short.")
    suggestion = answer.get("suggestion")
    if suggestion:
        out()
        out(
            "No precedent to rely on. Once the question is settled, record it "
            f"(kind {suggestion.get('kind')}, tags {suggestion.get('tags') or 'none'})."
        )
    if answer.get("next"):
        out()
        out(str(answer["next"]))


def cmd_decide(args: argparse.Namespace) -> None:
    from cgh.core.config import find_cgh_root

    as_json = bool(args.json)
    root = find_cgh_root(Path(args.root))
    if root is None:
        _decide_fail("Not inside a cgh project (no .cgh/ found)", as_json)
        return
    from cgh.server.tools_decision import decide_answer

    try:
        answer = decide_answer(root, args.question, args.namespace, args.limit)
    except Exception as exc:
        _decide_fail(f"decide failed: {exc}", as_json)
        return
    if "error" in answer:
        _decide_fail(str(answer["error"]), as_json)
        return
    if as_json:
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return
    _print_text(answer)
