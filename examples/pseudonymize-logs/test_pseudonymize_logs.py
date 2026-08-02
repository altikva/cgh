# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The pseudonym contract: stable under the same secret,
#              different under another secret, never the raw value,
#              and short secrets are refused.

from __future__ import annotations

import pytest

from codegraph import sdk

SECRET = b"0123456789abcdef0123456789abcdef"


def test_stable_and_keyed():
    a = sdk.pseudonymize("pii.email", "jeanne@example.com", SECRET)
    b = sdk.pseudonymize("pii.email", "jeanne@example.com", SECRET)
    other = sdk.pseudonymize("pii.email", "jeanne@example.com", b"x" * 32)
    assert a == b
    assert a != other
    assert "jeanne" not in a


def test_short_secret_refused():
    with pytest.raises(ValueError, match="16 bytes"):
        sdk.pseudonymize("pii.email", "jeanne@example.com", b"short")
