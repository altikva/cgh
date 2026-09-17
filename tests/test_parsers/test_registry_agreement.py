# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: is_supported and get_parser_for_path must agree. They did not:
#              filenames mapped to an extension key with no parser behind it
#              (Dockerfile, Makefile) answered "supported" and then produced no
#              parser, so every scan counted them as errors instead of skips.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.parsers import (
    get_parser_for_path,
    get_supported_extensions,
    is_supported,
)

NAMED_FILES = ["Dockerfile", "Dockerfile.dev", "Makefile", "docker-compose.yml"]


@pytest.mark.parametrize(
    "name",
    [
        *NAMED_FILES,
        "app.py",
        "main.ts",
        "README.md",
        "LICENSE",
        "uv.lock",
        ".gitignore",
    ],
)
def test_support_matches_parser_availability(name):
    path = Path(name)

    assert is_supported(path) == (get_parser_for_path(path) is not None), name


def test_a_named_file_without_a_parser_is_not_supported():
    # `.dockerfile` has no parser registered; saying otherwise turned every
    # Dockerfile in every repo into a scan error.
    assert ".dockerfile" not in get_supported_extensions()
    assert not is_supported(Path("Dockerfile"))


def test_a_named_file_with_a_parser_stays_supported():
    assert ".yaml" in get_supported_extensions()
    assert is_supported(Path("docker-compose.yml"))
