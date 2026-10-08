# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The egress gate. Decides, from the finding store, whether
#              a file's content may reach a cloud backend. Two postures,
#              set by [plugin.summarize] egress: open (the default, block
#              on what the store knows) and strict (allowlist, only files
#              explicitly labeled non-confidential go out). Local backends never
#              consult the gate: nothing leaves the machine.

from __future__ import annotations

from pathlib import Path


def egress_posture(repo_root: str | Path, config: dict) -> str:
    """ "open" or "strict". "open" unless the plugin's own egress key asks
    for "strict" (cgh's global secure mode was removed in 0.15.0)."""
    explicit = str(config.get("egress", "")).strip().lower()
    return "strict" if explicit == "strict" else "open"


def cloud_allowed(
    repo_root: str | Path, file_path: str, config: dict
) -> tuple[bool, str]:
    """May this file's content be sent to a cloud backend?

    Returns (allowed, reason). The reason is written to the audit log on
    deny so "why was this file skipped" has an answer.
    """
    from codegraph.plugin_api import findings_for_file

    rows = findings_for_file(repo_root, str(file_path))
    confidential = None
    for row in rows:
        if row["key"] == "confidential":
            confidential = str(row["value"]).strip().lower() in ("true", "yes", "1")
    if confidential:
        return False, "confidential finding"

    for row in rows:
        if row["severity"] == "block":
            return False, f"block-severity finding {row['key']}"

    if not config.get("allow_pii", False):
        for row in rows:
            if row["key"].startswith("pii."):
                return False, f"pii finding {row['key']} (allow_pii = false)"

    if egress_posture(repo_root, config) == "strict":
        if confidential is False:
            return True, "labeled non-confidential"
        return False, "strict posture: file not labeled non-confidential"

    return True, "gate clear"
