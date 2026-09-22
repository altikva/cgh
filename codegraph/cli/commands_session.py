# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Session continuity handlers wired into agent lifecycle
#              hooks, plus `cgh memory review`. _hook_checkpoint records
#              an automatic marker on PreCompact / SessionEnd so no clear
#              goes untracked even when the model forgot to checkpoint;
#              _hook_resume_header prints the tiny SessionStart header
#              announcing the bundle (the full bundle loads on demand,
#              so it only costs tokens when used).

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _find_root(payload: dict) -> Path | None:
    from codegraph.core.config import find_codegraph_root

    return find_codegraph_root(payload.get("cwd") or os.getcwd())


def cmd_hook_checkpoint(args: argparse.Namespace) -> None:
    """Automatic checkpoint marker on PreCompact / SessionEnd. The model
    writes real digests through the checkpoint MCP tool; this hook only
    guarantees the event itself is never lost."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        root = _find_root(payload)
        if root is None:
            return
        session_id = str(payload.get("session_id", "") or "")
        trigger = str(
            payload.get("trigger") or payload.get("hook_event_name") or "lifecycle"
        )
        from codegraph.state.call_log import knowledge_list, knowledge_record

        # One auto-marker per session, superseded on repeat, so PreCompact
        # storms never pile up rows.
        previous = knowledge_list(
            tag="auto-checkpoint", session_id=session_id, limit=1, repo_root=root
        )
        stamp = time.strftime("%Y-%m-%d %H:%M")
        knowledge_record(
            title=f"Auto checkpoint ({trigger})",
            body=(
                f"Context event '{trigger}' at {stamp} for session "
                f"{session_id or 'unknown'}. If no model-written digest exists "
                "for this session, its details are gone; standing instructions "
                "and knowledge below survive."
            ),
            kind="note",
            tags="auto-checkpoint,session-digest",
            session_id=session_id,
            repo_root=root,
            supersedes=previous[0]["id"] if previous else 0,
        )
    except Exception:
        pass  # a lifecycle hook must never break the session


def cmd_hook_resume_header(args: argparse.Namespace) -> None:
    """The SessionStart header: two or three plain lines on stdout that
    the agent cannot miss, full bundle on demand via the resume tool.
    Prints nothing when the store is empty, so fresh repos stay silent."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        root = _find_root(payload)
        if root is None:
            return
        from codegraph.core.fts import get_fts_conn, list_plan_entries
        from codegraph.state.call_log import knowledge_list

        instructions = len(
            knowledge_list(kind="standing_instruction", limit=50, repo_root=root)
        )
        digest_entries = knowledge_list(tag="session-digest", limit=50, repo_root=root)
        digests = len(digest_entries)
        try:
            plans = len(list_plan_entries(get_fts_conn(root), limit=50))
        except Exception:
            plans = 0

        # A SessionStart fired with source "compact" means the conversation was
        # just summarized: everything not already written down is gone. PreCompact
        # cannot inject text (it is purely advisory), so this is the one hook that
        # can push the model to record a real digest. Nudge whenever no
        # model-written digest exists for this session (auto-checkpoint markers,
        # which the lifecycle hook drops on its own, do not count).
        if str(payload.get("source") or "") == "compact":
            session_id = str(payload.get("session_id", "") or "")
            model_digests = [
                e
                for e in knowledge_list(
                    tag="session-digest",
                    session_id=session_id,
                    limit=50,
                    repo_root=root,
                )
                if "auto-checkpoint" not in str(e.get("tags", "") or "")
            ]
            if not model_digests:
                print(
                    "This session was just compacted and cgh has NO "
                    "model-written digest for it, so the summary above is all "
                    "that survived. Call the codegraph `compact_session` tool "
                    "now with what you did, the decisions you made, and the open "
                    "threads, so the next compaction does not lose them."
                )

        if not (instructions or digests):
            return
        print(
            "cgh holds a resume bundle for this project: "
            f"{instructions} standing instruction(s), {digests} session "
            f"digest(s), {plans} plan(s)."
        )
        print(
            "Call the codegraph `resume` tool (optionally with your task) "
            "to load it before re-deriving anything."
        )
    except Exception:
        pass


def cmd_memory(args: argparse.Namespace) -> None:
    """`cgh memory review`: the hygiene pass. Lists superseded entries
    and entries older than the window so a human (or an agent asked to
    tidy) can prune with knowledge_forget."""
    from rich.console import Console

    from codegraph.state.call_log import knowledge_list

    console = Console()
    root = Path(os.path.abspath(args.root))
    days = getattr(args, "days", 90)
    cutoff = time.time() - days * 86400

    entries = knowledge_list(limit=1000, repo_root=root)
    stale = [e for e in entries if e["ts"] < cutoff]
    if not entries:
        console.print("[dim]Knowledge store is empty.[/dim]")
        return
    console.print(
        f"[bold]entries:[/bold] {len(entries)} active, {len(stale)} older "
        f"than {days} day(s)"
    )
    if not stale:
        console.print("[dim]Nothing needs review.[/dim]")
        return
    console.print(f"[bold]Older than {days} day(s), consider pruning:[/bold]")
    for e in stale[:30]:
        age = int((time.time() - e["ts"]) / 86400)
        console.print(f"  #{e['id']:<5} {age:>4}d  [{e['kind']}] {e['title'][:70]}")
    console.print(
        "[dim]Drop one with the[/dim] [cyan]knowledge_forget[/cyan] "
        "[dim]MCP tool, or supersede it with a fresh entry.[/dim]"
    )
