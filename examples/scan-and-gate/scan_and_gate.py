# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The canonical embedding loop: scan a document for PII
#              with the installed scanners, then let the egress gate
#              decide whether the content may reach a cloud model.
#              Requires: pip install cgh cgh-pii

from __future__ import annotations

from codegraph import sdk

DOCUMENT = """Invoice 2026-118
Bill to: Jeanne Martin <jeanne.martin@example.com>
Server: prod-db-01 (10.20.30.40)
Total: 1,240 EUR
"""


def call_cloud_model(text: str) -> str:
    return "(pretend a cloud model answered here)"


def main() -> None:
    findings = sdk.scan_text(DOCUMENT, path="invoice.txt", scanners=["pii"])
    print(f"{len(findings)} finding(s):")
    for f in findings:
        print(f"  {f.key:20s} line {f.line}  severity={f.severity}")

    # pii blocks cloud unless explicitly allowed
    verdict = sdk.egress_decision(findings)
    print(f"\nallow_pii=False -> allowed={verdict.allowed} ({verdict.reason})")

    # the caller decided this PII may leave (e.g. a human reviewed it)
    verdict = sdk.egress_decision(findings, allow_pii=True)
    print(f"allow_pii=True  -> allowed={verdict.allowed}")

    if verdict:
        print(call_cloud_model(DOCUMENT))
    else:
        print("kept local")


if __name__ == "__main__":
    main()
