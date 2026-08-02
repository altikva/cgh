# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The scan + gate contract, fully offline: the regex tier
#              finds the planted PII and both gate postures answer the
#              way the README promises.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_pii")

from codegraph import sdk


def test_scan_finds_the_planted_pii():
    findings = sdk.scan_text(
        "contact jeanne.martin@example.com on 10.20.30.40", scanners=["pii"]
    )
    keys = {f.key for f in findings}
    assert any(k.startswith("pii.") for k in keys)


def test_assist_blocks_pii_by_default():
    findings = sdk.scan_text("mail me: someone@example.com", scanners=["pii"])
    assert not sdk.egress_decision(findings, mode="assist")
    assert sdk.egress_decision(findings, mode="assist", allow_pii=True)


def test_secure_requires_the_human_label():
    clean: list = []
    assert not sdk.egress_decision(clean, mode="secure")
    assert sdk.egress_decision(clean, mode="secure", labeled_non_confidential=True)
