# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Summarize text through the cgh-summarize backends with
#              the safe default: cloud_allowed=False restricts the pick
#              to local backends (an Ollama daemon if present, else the
#              model-free structural fallback). Pass your own egress
#              verdict to open the cloud path deliberately.
#              Requires: pip install cgh cgh-summarize

from __future__ import annotations

from codegraph import sdk

TEXT = """def reconcile(payments, ledger):
    matched, orphans = [], []
    for p in payments:
        entry = ledger.pop(p.reference, None)
        (matched if entry else orphans).append(p)
    return matched, orphans, ledger  # leftover entries are unpaid
"""


def main() -> None:
    summary = sdk.summarize(TEXT)  # local backends only, never leaves
    print(summary or "(no local backend available, install/start Ollama)")

    findings = sdk.scan_text(TEXT, scanners=["pii"])
    verdict = sdk.egress_decision(findings, mode="assist")
    summary = sdk.summarize(TEXT, cloud_allowed=bool(verdict))
    print(summary)


if __name__ == "__main__":
    main()
