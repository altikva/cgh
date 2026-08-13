# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A short-lived result cache for the vision pipeline. Vision
#              inference is slow, so re-running the same image (a first pass
#              with no --out, then again with --out) should not recompute.
#              The key is the image bytes plus the parameters that change
#              the result (models, profile, hint, num_ctx), so a different
#              profile never returns another profile's answer. Cached JSON
#              lives in a temp dir with a TTL (default 24h); --force bypasses
#              it. A cache miss or any error just recomputes, never breaks.

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

_TTL_HOURS_DEFAULT = 24.0


def _cache_dir(config: dict) -> Path:
    d = config.get("cache_dir")
    base = Path(d) if d else Path(tempfile.gettempdir()) / "cgh-vision-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_key(image_bytes: bytes, config: dict) -> str:
    """A digest over the image AND the result-affecting parameters. Two runs
    with the same image but a different profile / model / hint / num_ctx get
    different keys, so the cache never returns the wrong answer."""
    from .pipeline import profile_for

    prof = profile_for(config)
    parts = {
        "sha": hashlib.sha256(image_bytes).hexdigest(),
        "nodes": prof.get("nodes_model"),
        "edges": prof.get("edges_model"),
        "fallback": prof.get("fallback_model"),
        "profile": config.get("profile", "default"),
        "hint": config.get("hint", ""),
        "num_ctx": config.get("num_ctx", 8192),
    }
    blob = json.dumps(parts, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(config: dict, image_bytes: bytes) -> dict | None:
    """The cached result for this image+params if present and within TTL,
    else None. cache_ttl_hours <= 0 disables reads."""
    ttl = float(config.get("cache_ttl_hours", _TTL_HOURS_DEFAULT))
    if ttl <= 0:
        return None
    path = _cache_dir(config) / f"{cache_key(image_bytes, config)}.json"
    try:
        if path.is_file() and (time.time() - path.stat().st_mtime) < ttl * 3600:
            return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return None


def put(config: dict, image_bytes: bytes, result: dict) -> None:
    """Store a result. Best effort: a failed write never breaks the run."""
    if float(config.get("cache_ttl_hours", _TTL_HOURS_DEFAULT)) <= 0:
        return
    try:
        path = _cache_dir(config) / f"{cache_key(image_bytes, config)}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
