# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The egress_decision verdict: clean file clears, confidential and
#              block-severity and PII findings are refused, allow_pii opens the
#              PII path, and strict posture demands an explicit
#              non-confidential label.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codewrite")

from cgh_codewrite.gate import egress_decision

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


def _label(root, file: str, key: str, value: str = "true", severity: str = "info"):
    store.record_findings(
        root, file, "test", [ScanFinding(key=key, value=value, severity=severity)]
    )


def test_clean_file_clears_in_open_posture(tmp_path):
    allowed, reason = egress_decision(tmp_path, "a.py", {"egress": "open"})
    assert allowed and reason == "gate clear"


def test_confidential_finding_is_refused(tmp_path):
    _label(tmp_path, "secret.py", "confidential", "true")
    allowed, reason = egress_decision(tmp_path, "secret.py", {"egress": "open"})
    assert not allowed and "confidential" in reason


def test_block_severity_is_refused(tmp_path):
    _label(tmp_path, "danger.py", "secret.aws_key", "x", severity="block")
    allowed, reason = egress_decision(tmp_path, "danger.py", {"egress": "open"})
    assert not allowed and "block-severity" in reason


def test_pii_refused_unless_allowed(tmp_path):
    _label(tmp_path, "people.py", "pii.email", "a@b.c")
    allowed, _ = egress_decision(tmp_path, "people.py", {"egress": "open"})
    assert not allowed
    allowed2, _ = egress_decision(
        tmp_path, "people.py", {"egress": "open", "allow_pii": True}
    )
    assert allowed2


def test_strict_posture_requires_explicit_non_confidential_label(tmp_path):
    # Unlabeled file: refused under strict.
    allowed, reason = egress_decision(tmp_path, "plain.py", {"egress": "strict"})
    assert not allowed and "strict" in reason
    # Explicitly labeled non-confidential: allowed.
    _label(tmp_path, "clean.py", "confidential", "false")
    allowed2, reason2 = egress_decision(tmp_path, "clean.py", {"egress": "strict"})
    assert allowed2 and "non-confidential" in reason2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
