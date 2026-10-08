# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The egress_decision verdict: clean file clears, confidential and
#              block-severity and PII findings are refused, allow_pii opens the
#              PII path, strict posture demands an explicit
#              non-confidential label, and an unknown egress value fails
#              closed to strict.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.gate import egress_decision

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


def test_posture_defaults_to_open_even_with_legacy_secure_mode(tmp_path):
    from cgh_codegen.gate import egress_posture

    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nmode = "secure"\n', encoding="utf-8"
    )
    assert egress_posture(tmp_path, {}) == "open"
    assert egress_posture(tmp_path, {"egress": "strict"}) == "strict"


@pytest.mark.parametrize("value", [None, ""])
def test_absent_or_empty_egress_is_open(tmp_path, value):
    from cgh_codegen.gate import egress_posture

    config = {} if value is None else {"egress": value}
    assert egress_posture(tmp_path, config) == "open"
    assert egress_decision(tmp_path, "a.py", config)[0]


@pytest.mark.parametrize("value", ["stict", "Open", " open", "OPEN", "none", True, 1])
def test_unknown_egress_value_fails_closed(tmp_path, value):
    from cgh_codegen.gate import egress_posture

    assert egress_posture(tmp_path, {"egress": value}) == "strict"
    allowed, reason = egress_decision(tmp_path, "a.py", {"egress": value})
    assert not allowed
    assert "treated as strict" in reason


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
