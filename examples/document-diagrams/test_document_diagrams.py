# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Runs without Ollama and without sample documents: builds
#              a minimal docx (a zip with a word/media entry) on the
#              fly and fakes the model transport. Shows the seams to
#              use in your own suite.

from __future__ import annotations

import zipfile

import pytest

pytest.importorskip("cgh_vision")

from document_diagrams import images_from_office


def _fake_docx(path, image_bytes: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<document/>")
        zf.writestr("word/media/image1.png", image_bytes)
        zf.writestr("word/media/icon.png", b"tiny")  # under MIN_BYTES


def test_office_extraction_skips_icons(tmp_path):
    doc = tmp_path / "note.docx"
    _fake_docx(doc, b"\x89PNG" + b"0" * 8000)
    out = images_from_office(doc, tmp_path)
    assert [p.name for p in out] == ["note__image1.png"]


def test_extracted_image_flows_through_the_sdk(tmp_path, monkeypatch):
    import cgh_vision.pipeline as pipeline

    from codegraph import sdk

    doc = tmp_path / "note.docx"
    _fake_docx(doc, b"\x89PNG" + b"0" * 8000)
    (img,) = images_from_office(doc, tmp_path)

    replies = [
        '{"summary": "an archi", "content": ["architecture_diagram"],'
        ' "text_density": "sparse"}'
    ]
    monkeypatch.setattr(
        pipeline, "ask", lambda *a, **k: replies.pop(0) if replies else "{}"
    )
    inv = sdk.image_inventory(img)
    assert inv["content"] == ["architecture_diagram"]
