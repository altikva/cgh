# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The vision result cache: a round-trip keyed by image + params,
#              a different profile never returns another profile's answer,
#              a TTL of 0 disables it, and cgh vision reuses a cached result
#              on a re-run while --force recomputes.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_vision")

from cgh_vision import cache, cli


def test_round_trip_and_key_sensitivity(tmp_path):
    cfg = {"cache_dir": str(tmp_path), "profile": "default"}
    img = b"some-image-bytes"
    result = {"image": "x.png", "inventory": {"summary": "s", "content": ["diagram"]}}

    assert cache.get(cfg, img) is None
    cache.put(cfg, img, result)
    assert cache.get(cfg, img) == result
    # A different profile is a different key: never the wrong answer.
    assert cache.get({**cfg, "profile": "fast"}, img) is None
    # A different image is a different key.
    assert cache.get(cfg, b"other-bytes") is None


def test_ttl_zero_disables(tmp_path):
    cfg = {"cache_dir": str(tmp_path), "cache_ttl_hours": 0}
    img = b"x"
    cache.put(cfg, img, {"image": "x"})
    assert cache.get(cfg, img) is None


def test_cli_reuses_cache_and_force_recomputes(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    config = {"cache_dir": str(tmp_path / "cache")}

    calls = {"n": 0}

    def _fake_route(image, cfg, progress=None):
        calls["n"] += 1
        return {
            "image": "x.png",
            "inventory": {"summary": "s", "content": ["diagram"]},
            "diagram": None,
            "tables": [],
            "charts": [],
            "text": None,
        }

    monkeypatch.setattr("cgh_vision.pipeline.route_structured", _fake_route)

    first = cli._extract_one(img, config)
    assert calls["n"] == 1
    second = cli._extract_one(img, config)  # served from cache
    assert calls["n"] == 1 and second == first
    cli._extract_one(img, config, force=True)  # bypasses the cache
    assert calls["n"] == 2
