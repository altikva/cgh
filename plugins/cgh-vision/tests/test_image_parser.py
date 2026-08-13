# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The image parser makes an image an indexed file (one section,
#              filename as title, no scan_text) so the deferred vision
#              scanner can reach it. Without a claiming parser the indexer
#              skips the file entirely and the scanner never fires.

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cgh_vision")

from cgh_vision.image_parser import IMAGE_EXTENSIONS, ImageParser


def test_parse_marks_image_indexed_without_text():
    idx = ImageParser().parse(Path("/repo/diagram.png"))
    assert idx.lang == "image"
    assert len(idx.sections) == 1
    assert idx.sections[0].title == "diagram.png"
    # No text is extracted: images carry none, the vision scanner reads pixels.
    assert idx.scan_text == ""


def test_claims_the_expected_extensions():
    assert set(IMAGE_EXTENSIONS) == {".png", ".jpg", ".jpeg", ".webp"}
    assert ImageParser.extensions == IMAGE_EXTENSIONS
