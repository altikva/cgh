# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: is_loopback_url decides whether a "local backend" claim
#              is earned. Anything ambiguous or unparsable must come
#              back False: egress classification fails closed.

from __future__ import annotations

import pytest

from codegraph.core.utils import is_loopback_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://LOCALHOST:11434",
        "http://127.0.0.53",
        "http://[::1]:11434",
        "127.0.0.1:11434",  # scheme-less config values still parse
    ],
)
def test_loopback(url):
    assert is_loopback_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20:11434",  # LAN GPU box: leaves the machine
        "https://ollama.example.com",
        "http://10.0.0.5",
        "http://[fe80::1]",
        "",
        "not a url at all",
        "http://",
    ],
)
def test_not_loopback(url):
    assert is_loopback_url(url) is False
