# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The egress decision for cgh-codewrite. A reference file is
#              source that leaves the machine to reach a cloud model, so it
#              must clear the same confidentiality / PII / severity checks the
#              rest of cgh applies. The whole verdict lives in ONE function,
#              egress_decision, so there is a single place to audit and, later,
#              a single call site to swap for a shared core decision.
#
#              Consolidation note: this mirrors the egress logic the
#              cgh-summarize plugin also carries. Two copies of a security
#              verdict drift toward the leak, so the intended end state is one
#              shared decision in cgh core that every model-backed plugin
#              calls. Until that lands, keep this a single function, never
#              re-derive the verdict inline elsewhere in the plugin.

from __future__ import annotations

from pathlib import Path


def egress_posture(repo_root: str | Path, config: dict) -> str:
    """ "open" or "strict". An explicit config egress key wins; otherwise the
    global cgh mode decides (secure = strict)."""
    explicit = str(config.get("egress", "")).strip().lower()
    if explicit in ("open", "strict"):
        return explicit
    from codegraph.plugin_api import load_config

    return "strict" if load_config(repo_root).mode == "secure" else "open"


def egress_decision(
    repo_root: str | Path, file_path: str | Path, config: dict
) -> tuple[bool, str]:
    """May this file's content be sent to a cloud model? Returns
    (allowed, reason); the reason is meant for the audit line on a deny so
    "why was this reference refused" has an answer.

    This is the single source of the verdict for the plugin. Do not inline
    any part of it elsewhere: a second copy of a security decision is how a
    confidential file eventually slips out while the log still says audited.
    """
    from codegraph.plugin_api import findings_for_file

    rows = findings_for_file(repo_root, str(file_path))

    confidential: bool | None = None
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
