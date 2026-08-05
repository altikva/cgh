# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: redact_text over the regex categories runs offline with
#              cgh-pii alone (no NER model, no network). Names are a
#              separate test that skips without the NER extra.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_pii")

from codegraph import sdk


def test_regex_categories_redacted_stably():
    text = "mail a@x.com and again a@x.com"
    out = sdk.redact_text(text, only=["email"])
    assert out == "mail [EMAIL_1] and again [EMAIL_1]"


def test_only_leaves_other_pii_alone():
    text = "a@x.com paid via IBAN FR7630006000011234567890189"
    out = sdk.redact_text(text, only=["email"])
    assert "[EMAIL_1]" in out
    assert "FR7630006000011234567890189" in out  # iban not requested


def test_pseudonym_is_keyed_and_hides_the_value():
    out = sdk.redact_text("a@x.com", only=["email"], mode="pseudonym", secret=b"k" * 32)
    assert out.startswith("<pii.email:") and "a@x.com" not in out


def test_names_need_ner():
    """Without presidio, asking for person fails clearly."""
    pytest.importorskip("cgh_pii")
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        with pytest.raises(Exception, match="NER"):
            sdk.redact_text("Jeanne Martin", only=["person"])
