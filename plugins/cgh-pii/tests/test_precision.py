# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Precision of the regex card / phone validators: real values
#              still match, while the coincidental Luhn passes and spaced
#              digit runs that number-heavy documents produce are rejected.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_pii")

from cgh_pii.regex_scanner import _card_ok, _phone_ok


@pytest.mark.parametrize(
    "pan",
    ["4111111111111111", "5500005555555559", "340000000000009", "6011000990139424"],
)
def test_real_cards_pass(pan):
    assert _card_ok(pan) is True


@pytest.mark.parametrize(
    "pan",
    [
        "0000000000000000",  # uniform
        "1234567890123452",  # monotone ascending (Luhn-valid filler)
        "1990199019901990",  # bad IIN (starts with 1)
        "9999999999999999",  # bad IIN + uniform
    ],
)
def test_card_false_positives_rejected(pan):
    assert _card_ok(pan) is False


@pytest.mark.parametrize("num", ["+33 6 12 34 56 78", "+14155552671", "0033612345678"])
def test_real_phones_pass(num):
    assert _phone_ok(num) is True


@pytest.mark.parametrize(
    "num",
    [
        "+1 2 3 4 5 6 7 8 9 0 1 2",  # single digits spaced out: diagram noise
        "0012",  # too short
        "+123456789012345678",  # too long for E.164
    ],
)
def test_phone_false_positives_rejected(num):
    assert _phone_ok(num) is False
