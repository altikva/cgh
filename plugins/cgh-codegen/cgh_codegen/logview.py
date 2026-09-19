# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The human's model-free view of what codegen did. codegen already
#              audits every generation to the shared activity log; codegen_activity
#              reads that log back and keeps only codegen's own events, so the
#              CLI can show the work without a single line entering an agent's
#              context. This is the token-cheap surface: results are read from
#              disk, never relayed through the expensive model.

from __future__ import annotations

from pathlib import Path

# The events codegen writes via flow._audit. Kept in one place so the log view
# and the writer cannot silently disagree about what counts as codegen activity.
CODEGEN_EVENTS = (
    "codegen_generated",
    "codegen_extended",
    "codegen_egress_denied",
)


def codegen_activity(
    repo_root: str | Path, limit: int = 20, scan: int = 1000
) -> list[tuple[float, str, str]]:
    """The recent codegen entries from the repo's activity log, oldest first,
    as ``(ts, event, detail)`` tuples.

    Reads at most the last ``scan`` activity entries (the log holds every
    kind of event, not just codegen's), keeps the codegen ones, and returns
    the last ``limit`` of those. An empty list means codegen has done nothing
    here, or nothing within the scan window.
    """
    from codegraph.plugin_api import activity_tail

    entries = activity_tail(str(repo_root), max(1, scan))
    hits = [e for e in entries if len(e) >= 2 and e[1] in CODEGEN_EVENTS]
    return hits[-max(0, limit) :]
