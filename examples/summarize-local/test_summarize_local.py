# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deterministic without any model: forcing the structural
#              backend exercises the whole pick + summarize path and
#              returns the excerpt fallback. Also proves only local
#              backends are ever picked.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_summarize")

from codegraph import sdk


def test_structural_backend_needs_nothing():
    text = "def add(a, b):\n    return a + b\n" * 20
    summary = sdk.summarize(text, {"backend": "structural"})
    assert summary
    assert "def add" in summary


def test_picked_backend_is_always_local():
    from cgh_summarize.backends import egress_of, pick_backend

    for cloud_allowed in (False, True):
        picked = pick_backend({}, cloud_allowed=cloud_allowed)
        assert picked is None or egress_of(picked, {}) == "local"


def test_remote_ollama_counts_as_cloud():
    from cgh_summarize.backends import OllamaBackend

    remote = {"ollama_url": "http://192.168.1.20:11434"}
    assert OllamaBackend().egress_class(remote) == "cloud"
