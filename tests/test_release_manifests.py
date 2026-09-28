# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Every manifest that ships cgh under a version number must carry
#              the pyproject version. The npm wrapper's publish job refuses a
#              package.json that differs from the tag and fails on its own
#              without blocking the rest of the release, so a missed bump kept
#              npm a release behind unnoticed. Checking here makes a release
#              PR fail instead of the tag.

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _json(rel: str) -> dict:
    return json.loads((_ROOT / rel).read_text(encoding="utf-8"))


def _npm_wrapper() -> str:
    # Also what the launcher reads at runtime to pick the release binary.
    return _json("packaging/npm/package.json")["version"]


def _claude_plugin() -> str:
    return _json(".claude-plugin/plugin.json")["version"]


def _marketplace_entry() -> str:
    # The entry for cgh itself; metadata.version is the marketplace's own
    # version and moves independently.
    entries = _json(".claude-plugin/marketplace.json")["plugins"]
    return next(p for p in entries if p["name"] == "cgh")["version"]


@pytest.mark.parametrize(
    "read", [_npm_wrapper, _claude_plugin, _marketplace_entry], ids=lambda f: f.__name__
)
def test_manifest_version_matches_pyproject(read):
    assert read() == _pyproject_version()
