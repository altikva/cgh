# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP decision tools over the knowledge store. check_action
#              checks an action (a shell command or a short phrase) against
#              the rule lines of this repo's standing instructions and answers
#              allowed, refused, ask or no_rule. It reads the repo store only,
#              calls no model and records nothing.

from __future__ import annotations

from cgh.server._budget import dumps_answer


def register(mcp) -> None:
    import cgh.server as _srv
    from cgh.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def check_action(action: str, session_id: str = "") -> str:
        """
        Check an action against the user's standing instructions BEFORE doing
        it: a shell command ("git push origin develop") or a short phrase
        ("merge PR 12", "tag v0.22.0"). Call it before anything hard to undo:
        a push, a tag, a release, a publish, a merge, a force or a delete.

        The verdict is one of:
          allowed  a rule of the user allows it. Never a default.
          refused  a rule of the user refuses it: do not proceed.
          ask      a rule of the user says to ask first.
          no_rule  no rule covers it (or cgh could not read the action,
                   `recognised` false): use your own judgment and ask the
                   user before anything hard to undo.

        A rule is a line `cgh-rule: <refuse|ask|allow> <verb> [key=glob ...]`
        in the body of a standing_instruction of this repo's store (see
        knowledge_record). `rule` gives the deciding one with its reason,
        `actions` how the input was read, `related` (with no_rule) the
        standing instructions that mention the subject without ruling on it.

        session_id: your session id. An allow rule your own session recorded
              does not count for it and is listed in `ignored`.

        Rules come from this repo's store only, never from a shared store.
        This is a reminder for an agent acting in good faith, not a sandbox.
        """
        from cgh.state.call_log import check_action as _check

        return dumps_answer(_check(action, session_id, _srv._root))
